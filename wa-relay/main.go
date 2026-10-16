// wa-relay receives Alertmanager webhooks and forwards them as WhatsApp messages
// through a self-hosted Whatsmeow/OpenWA server (POST {WA_SERVER_URL}/api/sendText).
//
// Config (environment, see env.sample):
//
//	WA_SERVER_URL  base URL of the WhatsApp server
//	WA_API_KEY     value of the "key" header
//	WA_TO          comma-separated recipients: 10-digit Indian numbers, full numbers
//	               with country code, or complete chat IDs (…@s.whatsapp.net / …@g.us)
//	LISTEN_ADDR    default 127.0.0.1:9097
//	GRAFANA_URL    optional link appended to every message
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync/atomic"
	"time"
)

type alert struct {
	Status      string            `json:"status"`
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	StartsAt    time.Time         `json:"startsAt"`
	EndsAt      time.Time         `json:"endsAt"`
}

type webhook struct {
	Status string  `json:"status"`
	Alerts []alert `json:"alerts"`
}

var (
	serverURL  = strings.TrimRight(os.Getenv("WA_SERVER_URL"), "/")
	apiKey     = os.Getenv("WA_API_KEY")
	grafanaURL = os.Getenv("GRAFANA_URL")
	recipients []string
	client     = &http.Client{Timeout: 20 * time.Second}

	received, sent, failed atomic.Int64
)

// chatID turns "9876543210" into "919876543210@s.whatsapp.net"; full IDs pass through.
func chatID(to string) string {
	to = strings.TrimSpace(to)
	if strings.Contains(to, "@") {
		return to
	}
	to = strings.TrimPrefix(to, "+")
	if len(to) == 10 {
		to = "91" + to
	}
	return to + "@s.whatsapp.net"
}

// skipLabels are noise in a phone notification.
var skipLabels = map[string]bool{"alertname": true, "severity": true, "instance_host": true, "__name__": true}

func format(w webhook) string {
	var b strings.Builder
	for i, a := range w.Alerts {
		if i > 0 {
			b.WriteString("\n\n")
		}
		name := a.Labels["alertname"]
		if a.Status == "resolved" {
			fmt.Fprintf(&b, "✅ *RESOLVED* %s", name)
		} else {
			icon := "🟠"
			if a.Labels["severity"] == "critical" {
				icon = "🔴"
			}
			fmt.Fprintf(&b, "%s *FIRING* [%s] %s", icon, a.Labels["severity"], name)
		}
		if s := a.Annotations["summary"]; s != "" {
			fmt.Fprintf(&b, "\n%s", s)
		}
		if d := a.Annotations["description"]; d != "" {
			fmt.Fprintf(&b, "\n%s", d)
		}
		keys := make([]string, 0, len(a.Labels))
		for k := range a.Labels {
			if !skipLabels[k] {
				keys = append(keys, k)
			}
		}
		sort.Strings(keys)
		for _, k := range keys {
			fmt.Fprintf(&b, "\n• %s: %s", k, a.Labels[k])
		}
		ist := time.FixedZone("IST", 5*3600+1800)
		fmt.Fprintf(&b, "\nsince %s", a.StartsAt.In(ist).Format("02 Jan 15:04 MST"))
		if a.Status == "resolved" && !a.EndsAt.IsZero() {
			fmt.Fprintf(&b, ", resolved %s", a.EndsAt.In(ist).Format("15:04 MST"))
		}
	}
	if grafanaURL != "" {
		fmt.Fprintf(&b, "\n\n%s", grafanaURL)
	}
	msg := b.String()
	if len(msg) > 3800 {
		msg = msg[:3800] + "\n…(truncated)"
	}
	return msg
}

func sendText(to, text string) error {
	body, _ := json.Marshal(map[string]string{"chatId": chatID(to), "text": text})
	req, err := http.NewRequest(http.MethodPost, serverURL+"/api/sendText", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("key", apiKey)
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		eb, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("whatsapp server: %s: %s", resp.Status, strings.TrimSpace(string(eb)))
	}
	return nil
}

func handleAlert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var wh webhook
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&wh); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	received.Add(1)
	msg := format(wh)
	var errs []string
	for _, to := range recipients {
		if err := sendText(to, msg); err != nil {
			failed.Add(1)
			// Never log the recipient or key; the error text comes from the WA server.
			log.Printf("send failed: %v", err)
			errs = append(errs, err.Error())
			continue
		}
		sent.Add(1)
	}
	if len(errs) > 0 {
		// 5xx makes Alertmanager retry.
		http.Error(w, strings.Join(errs, "; "), http.StatusBadGateway)
		return
	}
	w.WriteHeader(http.StatusOK)
}

func handleMetrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintf(w, "# TYPE wa_relay_webhooks_received_total counter\nwa_relay_webhooks_received_total %d\n", received.Load())
	fmt.Fprintf(w, "# TYPE wa_relay_messages_sent_total counter\nwa_relay_messages_sent_total %d\n", sent.Load())
	fmt.Fprintf(w, "# TYPE wa_relay_messages_failed_total counter\nwa_relay_messages_failed_total %d\n", failed.Load())
	configured := 0
	if serverURL != "" && apiKey != "" && len(recipients) > 0 {
		configured = 1
	}
	fmt.Fprintf(w, "# TYPE wa_relay_configured gauge\nwa_relay_configured %d\n", configured)
}

func main() {
	for _, t := range strings.Split(os.Getenv("WA_TO"), ",") {
		if strings.TrimSpace(t) != "" {
			recipients = append(recipients, t)
		}
	}
	if serverURL == "" || apiKey == "" || len(recipients) == 0 {
		log.Printf("WARNING: WA_SERVER_URL, WA_API_KEY or WA_TO not set; alerts will fail until configured")
	}
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = "127.0.0.1:9097"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/alert", handleAlert)
	mux.HandleFunc("/metrics", handleMetrics)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "ok\n") })
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Printf("wa-relay listening on %s, %d recipient(s)", addr, len(recipients))
	log.Fatal(srv.ListenAndServe())
}
