package web

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"pku-appointment/internal/automation"
	"pku-appointment/internal/config"
	"pku-appointment/internal/pku"
	"pku-appointment/internal/store"
)

func TestStaticPagesAndTOTP(t *testing.T) {
	directory := t.TempDir()
	configPath := filepath.Join(directory, "config.json")
	if err := os.WriteFile(configPath, []byte(`{"totp_secret":"JBSWY3DPEHPK3PXP"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	loader := config.Loader{Path: configPath}
	queue, err := store.Open(filepath.Join(directory, "test.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer queue.Close()
	pkuService := pku.NewService(loader)
	handler, err := New(loader, pkuService, queue, automation.New(loader, pkuService, queue)).Handler()
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/topt", "/history", "/appointment", "/stack", "/static/app.css", "/api/totp"} {
		request := httptest.NewRequest(http.MethodGet, path, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("GET %s returned %d: %s", path, response.Code, response.Body.String())
		}
	}
}
