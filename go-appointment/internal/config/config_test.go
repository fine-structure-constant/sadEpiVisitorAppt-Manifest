package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoaderDefaults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"account":{"username":"u","password":"p"},"totp_secret":"JBSWY3DPEHPK3PXP","smtp":{"host":"smtp.example.com","username":"me@example.com","password":"secret","from":"me@example.com"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := (Loader{Path: path}).Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Server.Host != "127.0.0.1" || cfg.Server.Port != 8000 {
		t.Fatalf("unexpected server defaults: %#v", cfg.Server)
	}
	if cfg.SMTP.Port != 587 || cfg.SMTP.To != "me@example.com" {
		t.Fatalf("unexpected SMTP defaults: %#v", cfg.SMTP)
	}
}
