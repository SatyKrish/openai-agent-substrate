// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const alice = "spiffe://applications.example/agent/alice"
const bob = "spiffe://applications.example/agent/bob"

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type readBody func([]byte) (int, error)

func (f readBody) Read(dst []byte) (int, error) { return f(dst) }

func grantsFile(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "grants.json")
	writeFile(t, path, []byte(`{"`+alice+`":["demo/one"],"`+bob+`":["demo/two"]}`))
	return path
}

func writeFile(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func uri(t *testing.T, value string) *url.URL {
	t.Helper()
	u, err := url.Parse(value)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

func request(t *testing.T, method, path, actor, identity string, body io.Reader) *http.Request {
	t.Helper()
	req := httptest.NewRequest(method, path, body)
	req.Header.Set(actorHeader, actor)
	req.Header.Set("Content-Type", "application/json")
	if identity != "" {
		req.TLS = &tls.ConnectionState{VerifiedChains: [][]*x509.Certificate{{{URIs: []*url.URL{uri(t, identity)}}}}}
	}
	return req
}

func TestRejectsOutOfScopeRequests(t *testing.T) {
	g := gateway{grantFile: grantsFile(t), client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
		t.Fatal("request with invalid or missing grant reached the upstream")
		return nil, nil
	})}}
	for _, tc := range []struct {
		name, method, path, actor, identity string
		want                                int
	}{
		{"unverified peer", http.MethodPost, processPath, "demo/one", "", 403},
		{"unrecognized peer", http.MethodPost, processPath, "demo/one", "spiffe://applications.example/agent/unknown", 403},
		{"different actor", http.MethodPost, processPath, "demo/two", alice, 403},
		{"eviction follows same grant", http.MethodPost, evictionPath, "demo/two", alice, 403},
		{"invalid Actor name", http.MethodPost, processPath, "demo/", alice, 400},
		{"unrecognized path", http.MethodPost, "/other", "demo/one", alice, 404},
		{"query", http.MethodPost, processPath + "?debug", "demo/one", alice, 404},
		{"wrong method", http.MethodGet, evictionPath, "demo/one", alice, 405},
	} {
		t.Run(tc.name, func(t *testing.T) {
			resp := httptest.NewRecorder()
			g.ServeHTTP(resp, request(t, tc.method, tc.path, tc.actor, tc.identity, nil))
			if resp.Code != tc.want {
				t.Fatalf("got status %d, want %d", resp.Code, tc.want)
			}
		})
	}
	resp := httptest.NewRecorder()
	req := request(t, http.MethodPost, processPath, "demo/one", alice, nil)
	req.Header.Add(actorHeader, "demo/two")
	g.ServeHTTP(resp, req)
	if resp.Code != http.StatusBadRequest {
		t.Fatalf("duplicate target: %d", resp.Code)
	}
	for _, tc := range []struct {
		path string
		size int
	}{{processPath, maxRequest + 1}, {evictionPath, 1}} {
		resp = httptest.NewRecorder()
		g.ServeHTTP(resp, request(t, http.MethodPost, tc.path, "demo/one", alice, strings.NewReader(strings.Repeat("x", tc.size))))
		if resp.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("oversized body for %s: %d", tc.path, resp.Code)
		}
	}
	writeFile(t, g.grantFile, []byte(`{}`))
	resp = httptest.NewRecorder()
	g.ServeHTTP(resp, request(t, http.MethodPost, processPath, "demo/one", alice, nil))
	if resp.Code != http.StatusForbidden {
		t.Fatalf("removed grant: %d", resp.Code)
	}

	writeFile(t, g.grantFile, []byte(`{"`+alice+`":`))
	resp = httptest.NewRecorder()
	g.ServeHTTP(resp, request(t, http.MethodPost, processPath, "demo/one", alice, nil))
	if resp.Code != http.StatusServiceUnavailable {
		t.Fatalf("broken policy: %d", resp.Code)
	}
}

func TestLoadsCurrentGrantsAfterBufferingRequest(t *testing.T) {
	policy := grantsFile(t)
	writeFile(t, policy, []byte(`{}`))
	command := `{"command":["true"]}`
	body := readBody(func(dst []byte) (int, error) {
		writeFile(t, policy, []byte(`{"`+alice+`":["demo/one"]}`))
		return copy(dst, command), io.EOF
	})
	g := gateway{upstream: *uri(t, "https://router.example"), grantFile: policy,
		client: &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
			if r.Header.Get(actorHeader) != "demo/one" {
				t.Fatal("gateway forwarded a different Actor")
			}
			return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{}`))}, nil
		})}}
	resp := httptest.NewRecorder()
	g.ServeHTTP(resp, request(t, http.MethodPost, processPath, "demo/one", alice, body))
	if resp.Code != http.StatusOK {
		t.Fatalf("gateway did not use grants current at forwarding: got %d", resp.Code)
	}
}

type certificate struct {
	cert, key, combined string
	parent              *x509.Certificate
	signer              ed25519.PrivateKey
}

func issue(t *testing.T, name, identity string, signingCA *certificate, usage x509.ExtKeyUsage) certificate {
	t.Helper()
	pub, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true,
		IPAddresses: []net.IP{net.IPv4(127, 0, 0, 1)},
	}
	if identity != "" {
		leaf.URIs = []*url.URL{uri(t, identity)}
	}
	parent, signer := leaf, key
	if signingCA == nil {
		leaf.IsCA = true
		leaf.KeyUsage |= x509.KeyUsageCertSign
	} else {
		parent, signer = signingCA.parent, signingCA.signer
		leaf.ExtKeyUsage = []x509.ExtKeyUsage{usage}
	}
	der, err := x509.CreateCertificate(rand.Reader, leaf, parent, pub, signer)
	if err != nil {
		t.Fatal(err)
	}
	keyBytes, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	cert := certificate{filepath.Join(dir, name+"-cert.pem"), filepath.Join(dir, name+"-key.pem"),
		filepath.Join(dir, name+"-bundle.pem"), parsed, key}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyBytes})
	writeFile(t, cert.cert, certPEM)
	writeFile(t, cert.key, keyPEM)
	writeFile(t, cert.combined, append(certPEM, keyPEM...))
	return cert
}

func serverWithTLS(t *testing.T, cert, clients certificate, handler http.Handler) *httptest.Server {
	t.Helper()
	config, err := downstreamTLS(cert.cert, cert.key, clients.cert)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewUnstartedServer(handler)
	server.TLS = config
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func TestMutualTLSForwardingUsesSeparateIdentities(t *testing.T) {
	applications := issue(t, "apps-ca", "", nil, 0)
	cluster := issue(t, "router-ca", "", nil, 0)
	gatewayIdentity := "spiffe://cluster.local/ns/execution-gateway/sa/actor-gateway"
	gatewayCaller := issue(t, "gateway-caller", gatewayIdentity, &cluster, x509.ExtKeyUsageClientAuth)
	gatewayServing := issue(t, "gateway-serving", "", &applications, x509.ExtKeyUsageServerAuth)
	routerServing := issue(t, "router-serving", "", &cluster, x509.ExtKeyUsageServerAuth)
	app := issue(t, "application", alice, &applications, x509.ExtKeyUsageClientAuth)
	wrongCA := issue(t, "application-wrong-ca", alice, &cluster, x509.ExtKeyUsageClientAuth)
	var forwarded atomic.Int32
	upstream := serverWithTLS(t, routerServing, cluster, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		if peerIdentity(r.TLS) != gatewayIdentity || r.Header.Get(actorHeader) != "demo/one" || r.Header.Get("X-Caller-ID") != "" {
			t.Errorf("unexpected forwarding identity, Actor or caller header")
		}
		switch r.URL.Path {
		case processPath:
			if r.Header.Get("Content-Type") != "application/json" {
				t.Error("upstream lost request content type")
			}
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Error(err)
			}
			if string(body) == `{"command":["redirect"]}` {
				http.Redirect(w, r, "/not-execution", http.StatusTemporaryRedirect)
				return
			}
			io.WriteString(w, `{"exitCode":0,"stdout":"ok","stderr":""}`)
		case evictionPath:
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Error(err)
			}
			if len(body) != 0 {
				t.Error("eviction poll must have an empty body")
			}
			io.WriteString(w, `{"eviction_id":17}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
		}
	}))
	gatewayToRouter, err := upstreamClient(gatewayCaller.combined, cluster.cert)
	if err != nil {
		t.Fatal(err)
	}
	gate := serverWithTLS(t, gatewayServing, applications, gateway{
		upstream: *uri(t, upstream.URL), client: gatewayToRouter, grantFile: grantsFile(t),
	})
	appToGateway, err := upstreamClient(app.combined, applications.cert)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		path, body string
		status     int
	}{
		{processPath, `{"command":["true"]}`, http.StatusOK},
		{processPath, `{"command":["redirect"]}`, http.StatusBadGateway},
		{evictionPath, "", http.StatusOK},
	} {
		r, err := http.NewRequest(http.MethodPost, gate.URL+tc.path, strings.NewReader(tc.body))
		if err != nil {
			t.Fatal(err)
		}
		r.Header.Set(actorHeader, "demo/one")
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("X-Caller-ID", bob)
		resp, err := appToGateway.Do(r)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != tc.status || resp.Header.Get("Cache-Control") != "no-store" {
			t.Fatalf("unexpected response: %v", resp)
		}
		if resp.Header.Get("Location") != "" {
			t.Error("gateway exposed an upstream redirect")
		}
	}
	untrusted, err := upstreamClient(wrongCA.combined, applications.cert)
	if err != nil {
		t.Fatal(err)
	}
	if resp, err := untrusted.Post(gate.URL+evictionPath, "application/json", nil); err == nil {
		resp.Body.Close()
		t.Fatal("gateway accepted a client certificate from the upstream trust domain")
	}
	if got := forwarded.Load(); got != 3 {
		t.Fatalf("router received %d calls, want 3", got)
	}
}
