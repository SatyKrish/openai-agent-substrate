// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0

// Compiled alongside the pinned upstream sandbox server.
package main

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
)

func init() {
	var notice atomic.Uint64
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM)
	go func() {
		for range signals {
			// Keep PID 1 alive so the harness can synchronously suspend the Actor.
			// A new notice must differ from host records even after a cold restore.
			var random [8]byte
			for {
				rand.Read(random[:])
				next := binary.LittleEndian.Uint64(random[:])
				if next != 0 && next != notice.Load() {
					notice.Store(next)
					break
				}
			}
		}
	}()
	http.HandleFunc("POST /_substrate/eviction", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.ContentLength != 0 {
			http.Error(w, "Eviction polls must have an empty body", http.StatusBadRequest)
			return
		}
		// The harness records this ID as handled only after a synchronous suspend.
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(struct {
			ID uint64 `json:"eviction_id"`
		}{ID: notice.Load()})
	})
}
