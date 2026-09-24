package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"pku-appointment/internal/model"
)

func TestJobLifecycle(t *testing.T) {
	queue, err := Open(filepath.Join(t.TempDir(), "queue.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer queue.Close()
	appointment := model.Appointment{ContactPhone: "13800000000", Date: "2026-10-01", Time: "09:00", Gate: "东门", Reason: "测试", VisitorName: "测试访客", VisitorID: "ABC12345", VisitorPhone: "13900000000"}
	job, err := queue.AddExisting(context.Background(), appointment, "remote-1", time.Now().Add(-time.Minute), 3)
	if err != nil {
		t.Fatal(err)
	}
	claimed, err := queue.ClaimDue(context.Background(), time.Now())
	if err != nil || claimed == nil || claimed.ID != job.ID || claimed.Status != "running" || claimed.Attempts != 1 {
		t.Fatalf("unexpected claim: %#v, %v", claimed, err)
	}
	if err := queue.Complete(context.Background(), job.ID, map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	completed, err := queue.Get(context.Background(), job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if completed.Status != "completed" || completed.EmailStatus != "pending" {
		t.Fatalf("unexpected completed job: %#v", completed)
	}
}
