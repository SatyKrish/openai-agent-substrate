// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0

// Compiled alongside the pinned upstream sandbox server.
package main

import (
	"encoding/json"
	"io"
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
		var next uint64
		for range signals {
			// Keep PID 1 alive so the harness can synchronously suspend the Actor.
			next++
			notice.Store(next)
		}
	}()
	http.HandleFunc("POST /_substrate/eviction", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64))
		if err != nil {
			http.Error(w, "Invalid eviction notice request", http.StatusBadRequest)
			return
		}
		if len(body) == 0 {
			// Leave the notice in place until the harness sees and acknowledges it.
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(struct {
				ID uint64 `json:"eviction_id"`
			}{ID: notice.Load()})
			return
		}
		var ack struct {
			ID uint64 `json:"acknowledge"`
		}
		if err := json.Unmarshal(body, &ack); err != nil || ack.ID == 0 {
			http.Error(w, "Invalid eviction acknowledgement", http.StatusBadRequest)
			return
		}
		// A stale acknowledgement must never clear a newer SIGTERM notice.
		notice.CompareAndSwap(ack.ID, 0)
		w.WriteHeader(http.StatusNoContent)
	})
}
