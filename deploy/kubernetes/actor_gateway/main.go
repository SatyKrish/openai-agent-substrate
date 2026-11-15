// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0

// actor-gateway authorizes requests to private Substrate execution Actors.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"time"
)

const (
	actorHeader     = "Ate-Target-Actor"
	processPath     = "/process"
	evictionPath    = "/_substrate/eviction"
	maxRequest      = 2 << 20
	maxResponse     = 4 << 20
	evictionLimit   = 64
	maxPolicy       = 1 << 20
	commandDeadline = 6 * time.Minute
	evictionTimeout = 3 * time.Second
)

var actorRef = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?/[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)

type gateway struct {
	upstream  url.URL
	client    *http.Client
	grantFile string
}

func peerIdentity(state *tls.ConnectionState) string {
	if state == nil || len(state.VerifiedChains) == 0 || len(state.VerifiedChains[0]) == 0 {
		return ""
	}
	names := state.VerifiedChains[0][0].URIs
	if len(names) != 1 {
		return ""
	}
	id := names[0]
	if id.Scheme != "spiffe" || id.Host == "" || id.User != nil || id.Port() != "" || id.Path == "" || id.ForceQuery || id.RawQuery != "" || id.Fragment != "" {
		return ""
	}
	return id.String()
}

func (g gateway) allowsActor(identity, actor string) (bool, error) {
	file, err := os.Open(g.grantFile)
	if err != nil {
		return false, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, maxPolicy+1))
	if err != nil || len(data) > maxPolicy {
		return false, fmt.Errorf("cannot read bounded actor grants")
	}
	var grants map[string][]string
	if err := json.Unmarshal(data, &grants); err != nil {
		return false, err
	}
	for _, ref := range grants[identity] {
		if ref == actor {
			return true, nil
		}
	}
	return false, nil
}

func (g gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	path := r.URL.EscapedPath()
	if (path != processPath && path != evictionPath) || r.URL.ForceQuery || r.URL.RawQuery != "" {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	identity := peerIdentity(r.TLS)
	if identity == "" {
		http.Error(w, "verified caller identity required", http.StatusForbidden)
		return
	}
	actors := r.Header.Values(actorHeader)
	if len(actors) != 1 || !actorRef.MatchString(actors[0]) {
		http.Error(w, "one exact Actor reference required", http.StatusBadRequest)
		return
	}
	actor := actors[0]
	allowed, err := g.allowsActor(identity, actor)
	if err != nil {
		http.Error(w, "Actor authorization unavailable", http.StatusServiceUnavailable)
		return
	}
	if !allowed {
		http.Error(w, "Actor access denied", http.StatusForbidden)
		return
	}
	requestLimit := int64(maxRequest)
	if path == evictionPath {
		requestLimit = evictionLimit
	} else {
		mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || mediaType != "application/json" {
			http.Error(w, "JSON body required", http.StatusUnsupportedMediaType)
			return
		}
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, requestLimit+1))
	if err != nil {
		http.Error(w, "could not read request body", http.StatusBadRequest)
		return
	}
	if int64(len(body)) > requestLimit {
		http.Error(w, "request body exceeds limit", http.StatusRequestEntityTooLarge)
		return
	}
	g.forward(w, r, path, actor, body)
}

func (g gateway) forward(w http.ResponseWriter, r *http.Request, path, actor string, body []byte) {
	deadline := commandDeadline
	responseLimit := int64(maxResponse)
	if path == evictionPath {
		deadline = evictionTimeout
		responseLimit = evictionLimit
	}
	ctx, cancel := context.WithTimeout(r.Context(), deadline)
	defer cancel()
	destination := g.upstream
	destination.Path = path
	upstream := &http.Request{
		Method:        http.MethodPost,
		URL:           &destination,
		Header:        http.Header{actorHeader: []string{actor}},
		ContentLength: int64(len(body)),
	}
	// Send only headers understood by Substrate. Keep upstream requests single-use.
	if len(body) != 0 {
		upstream.Body = io.NopCloser(bytes.NewReader(body))
		upstream.Header.Set("Content-Type", "application/json")
	}
	response, err := g.client.Do(upstream.WithContext(ctx))
	if err != nil {
		http.Error(w, "Actor request failed", http.StatusBadGateway)
		return
	}
	defer response.Body.Close()
	if path == evictionPath && response.StatusCode == http.StatusNoContent {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if response.StatusCode != http.StatusOK {
		code := response.StatusCode
		if code < 400 || code > 599 {
			code = http.StatusBadGateway
		}
		http.Error(w, "Actor request failed", code)
		return
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, responseLimit+1))
	if err != nil || int64(len(payload)) > responseLimit {
		http.Error(w, "could not read bounded Actor response", http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = w.Write(payload)
}

func loadRoots(path string) (*x509.CertPool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(data) {
		return nil, fmt.Errorf("no certificates in CA file %s", path)
	}
	return roots, nil
}

func downstreamTLS(certFile, keyFile, caFile string) (*tls.Config, error) {
	load := func() (*tls.Config, error) {
		cert, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			return nil, err
		}
		roots, err := loadRoots(caFile)
		if err != nil {
			return nil, err
		}
		return &tls.Config{Certificates: []tls.Certificate{cert}, ClientCAs: roots,
			ClientAuth: tls.RequireAndVerifyClientCert, MinVersion: tls.VersionTLS12, SessionTicketsDisabled: true}, nil
	}
	config, err := load()
	if err != nil {
		return nil, err
	}
	config.GetConfigForClient = func(*tls.ClientHelloInfo) (*tls.Config, error) { return load() }
	return config, nil
}

func upstreamClient(bundle, caFile string) (*http.Client, error) {
	load := func() (*tls.Config, error) {
		cert, err := tls.LoadX509KeyPair(bundle, bundle)
		if err != nil {
			return nil, err
		}
		roots, err := loadRoots(caFile)
		if err != nil {
			return nil, err
		}
		return &tls.Config{Certificates: []tls.Certificate{cert}, RootCAs: roots, MinVersion: tls.VersionTLS12}, nil
	}
	if _, err := load(); err != nil {
		return nil, err
	}
	transport := &http.Transport{
		DisableKeepAlives:  true,
		DisableCompression: true,
		TLSNextProto:       make(map[string]func(string, *tls.Conn) http.RoundTripper),
		DialTLSContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			// Projected certificates and trust bundles may rotate while the gateway runs.
			config, err := load()
			if err != nil {
				return nil, err
			}
			ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
			defer cancel()
			dialer := tls.Dialer{Config: config}
			return dialer.DialContext(ctx, network, addr)
		},
	}
	return &http.Client{Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, nil
}

func main() {
	listen := flag.String("listen", "127.0.0.1:8443", "address for the private HTTPS listener")
	router := flag.String("upstream", "https://atenet-router.ate-system.svc", "Substrate router HTTPS origin")
	grants := flag.String("grants", "", "JSON policy mapping caller SPIFFE IDs to exact Actor references")
	serverCert := flag.String("server-cert", "", "gateway serving certificate PEM")
	serverKey := flag.String("server-key", "", "gateway serving private key PEM")
	callerCA := flag.String("caller-ca", "", "CA bundle for verifying application certificates")
	routerBundle := flag.String("upstream-client-bundle", "", "gateway client certificate and key PEM for Substrate")
	routerCA := flag.String("upstream-ca", "", "CA bundle for verifying the Substrate router")
	flag.Parse()
	u, err := url.Parse(*router)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.ForceQuery || u.RawQuery != "" || u.Fragment != "" || (u.EscapedPath() != "" && u.EscapedPath() != "/") {
		log.Fatal("upstream must be an HTTPS origin")
	}
	if *grants == "" {
		log.Fatal("grants is required")
	}
	inbound, err := downstreamTLS(*serverCert, *serverKey, *callerCA)
	if err != nil {
		log.Fatalf("could not configure gateway TLS: %v", err)
	}
	client, err := upstreamClient(*routerBundle, *routerCA)
	if err != nil {
		log.Fatalf("could not configure upstream TLS: %v", err)
	}
	server := http.Server{
		Addr:              *listen,
		Handler:           gateway{upstream: *u, client: client, grantFile: *grants},
		TLSConfig:         inbound,
		TLSNextProto:      make(map[string]func(*http.Server, *tls.Conn, http.Handler)),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      commandDeadline + 20*time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    8 << 10,
	}
	server.SetKeepAlivesEnabled(false)
	log.Fatal(server.ListenAndServeTLS("", ""))
}
