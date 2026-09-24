package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"

	"pku-appointment/internal/model"
)

var ErrNotFound = errors.New("任务不存在")
var beijing = time.FixedZone("Asia/Shanghai", 8*60*60)

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", abs)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	for _, pragma := range []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA busy_timeout=15000",
		"PRAGMA foreign_keys=ON",
	} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, err
		}
	}
	store := &Store{db: db}
	if err := store.initialize(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	return store, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) initialize(ctx context.Context) error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS appointment_jobs (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			payload_json TEXT NOT NULL,
			scheduled_at INTEGER NOT NULL,
			status TEXT NOT NULL DEFAULT 'pending',
			attempts INTEGER NOT NULL DEFAULT 0,
			max_attempts INTEGER NOT NULL DEFAULT 3,
			next_attempt_at INTEGER,
			remote_id TEXT NOT NULL,
			result_json TEXT,
			last_error TEXT,
			email_status TEXT,
			email_error TEXT,
			created_at INTEGER NOT NULL,
			updated_at INTEGER NOT NULL,
			started_at INTEGER,
			finished_at INTEGER
		)`,
		`CREATE INDEX IF NOT EXISTS idx_appointment_jobs_due
		 ON appointment_jobs(status, scheduled_at, next_attempt_at)`,
		`CREATE UNIQUE INDEX IF NOT EXISTS idx_appointment_jobs_active_remote
		 ON appointment_jobs(remote_id)
		 WHERE status IN ('pending', 'retrying', 'running')`,
		`CREATE TABLE IF NOT EXISTS app_metadata (
			key TEXT PRIMARY KEY,
			value TEXT NOT NULL,
			updated_at INTEGER NOT NULL
		)`,
	}
	for _, statement := range statements {
		if _, err := s.db.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	now := time.Now().Unix()
	_, err := s.db.ExecContext(ctx, `UPDATE appointment_jobs
		SET status='retrying', next_attempt_at=?,
		last_error=CASE WHEN last_error IS NULL OR last_error='' THEN '应用重启，任务等待重新执行' ELSE last_error END,
		updated_at=? WHERE status='running'`, now, now)
	return err
}

func (s *Store) AddExisting(ctx context.Context, appointment model.Appointment, remoteID string, scheduledAt time.Time, maxAttempts int) (*model.Job, error) {
	if remoteID == "" {
		return nil, errors.New("申请编号不能为空")
	}
	if maxAttempts < 1 {
		maxAttempts = 1
	}
	if maxAttempts > 10 {
		maxAttempts = 10
	}
	payload, err := json.Marshal(appointment)
	if err != nil {
		return nil, err
	}
	now := time.Now().Unix()
	result, err := s.db.ExecContext(ctx, `INSERT INTO appointment_jobs
		(payload_json, scheduled_at, status, attempts, max_attempts, remote_id, created_at, updated_at)
		VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)`, string(payload), scheduledAt.Unix(), maxAttempts, remoteID, now, now)
	if err != nil {
		return nil, err
	}
	id, err := result.LastInsertId()
	if err != nil {
		return nil, err
	}
	return s.Get(ctx, id)
}

type scanner interface {
	Scan(dest ...any) error
}

func scanJob(row scanner) (*model.Job, error) {
	var job model.Job
	var payloadJSON string
	var resultJSON, lastError, emailStatus, emailError sql.NullString
	var nextAttempt, startedAt, finishedAt sql.NullInt64
	var scheduledAt, createdAt, updatedAt int64
	err := row.Scan(
		&job.ID, &payloadJSON, &scheduledAt, &job.Status, &job.Attempts,
		&job.MaxAttempts, &nextAttempt, &job.RemoteID, &resultJSON, &lastError,
		&emailStatus, &emailError, &createdAt, &updatedAt, &startedAt, &finishedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal([]byte(payloadJSON), &job.Appointment); err != nil {
		return nil, err
	}
	if resultJSON.Valid {
		if err := json.Unmarshal([]byte(resultJSON.String), &job.Result); err != nil {
			return nil, err
		}
	}
	job.LastError = lastError.String
	job.EmailStatus = emailStatus.String
	job.EmailError = emailError.String
	job.ScheduledAt = time.Unix(scheduledAt, 0).In(beijing)
	job.CreatedAt = time.Unix(createdAt, 0).In(beijing)
	job.UpdatedAt = time.Unix(updatedAt, 0).In(beijing)
	if nextAttempt.Valid {
		value := time.Unix(nextAttempt.Int64, 0).In(beijing)
		job.NextAttemptAt = &value
	}
	if startedAt.Valid {
		value := time.Unix(startedAt.Int64, 0).In(beijing)
		job.StartedAt = &value
	}
	if finishedAt.Valid {
		value := time.Unix(finishedAt.Int64, 0).In(beijing)
		job.FinishedAt = &value
	}
	return &job, nil
}

const jobColumns = `id, payload_json, scheduled_at, status, attempts, max_attempts,
	next_attempt_at, remote_id, result_json, last_error, email_status, email_error,
	created_at, updated_at, started_at, finished_at`

func (s *Store) Get(ctx context.Context, id int64) (*model.Job, error) {
	return scanJob(s.db.QueryRowContext(ctx, `SELECT `+jobColumns+` FROM appointment_jobs WHERE id=?`, id))
}

func (s *Store) ActiveForRemote(ctx context.Context, remoteID string) (*model.Job, error) {
	return scanJob(s.db.QueryRowContext(ctx, `SELECT `+jobColumns+` FROM appointment_jobs
		WHERE remote_id=? AND status IN ('pending','retrying','running') ORDER BY id DESC LIMIT 1`, remoteID))
}

func (s *Store) List(ctx context.Context, limit int) ([]model.Job, error) {
	if limit < 1 || limit > 500 {
		limit = 200
	}
	rows, err := s.db.QueryContext(ctx, `SELECT `+jobColumns+` FROM appointment_jobs
		ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 WHEN 'retrying' THEN 2 ELSE 3 END,
		COALESCE(next_attempt_at, scheduled_at), id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	jobs := make([]model.Job, 0)
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, *job)
	}
	return jobs, rows.Err()
}

func (s *Store) Counts(ctx context.Context) (map[string]int, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT status, COUNT(*) FROM appointment_jobs GROUP BY status`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	counts := map[string]int{}
	for rows.Next() {
		var status string
		var count int
		if err := rows.Scan(&status, &count); err != nil {
			return nil, err
		}
		counts[status] = count
	}
	return counts, rows.Err()
}

func (s *Store) ClaimDue(ctx context.Context, now time.Time) (*model.Job, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	job, err := scanJob(tx.QueryRowContext(ctx, `SELECT `+jobColumns+` FROM appointment_jobs
		WHERE status IN ('pending','retrying') AND scheduled_at<=?
		AND (next_attempt_at IS NULL OR next_attempt_at<=?)
		ORDER BY COALESCE(next_attempt_at, scheduled_at), id LIMIT 1`, now.Unix(), now.Unix()))
	if errors.Is(err, ErrNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	result, err := tx.ExecContext(ctx, `UPDATE appointment_jobs SET status='running', attempts=attempts+1,
		started_at=?, updated_at=?, next_attempt_at=NULL, last_error=NULL
		WHERE id=? AND status=?`, now.Unix(), now.Unix(), job.ID, job.Status)
	if err != nil {
		return nil, err
	}
	changed, err := result.RowsAffected()
	if err != nil || changed != 1 {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return s.Get(ctx, job.ID)
}

func (s *Store) Complete(ctx context.Context, id int64, result map[string]any) error {
	encoded, err := json.Marshal(result)
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	_, err = s.db.ExecContext(ctx, `UPDATE appointment_jobs SET status='completed', result_json=?,
		finished_at=?, updated_at=?, last_error=NULL, email_status='pending', email_error=NULL
		WHERE id=? AND status='running'`, string(encoded), now, now, id)
	return err
}

func (s *Store) Fail(ctx context.Context, id int64, failure error) error {
	job, err := s.Get(ctx, id)
	if err != nil {
		return err
	}
	now := time.Now()
	exhausted := job.Attempts >= job.MaxAttempts
	delay := time.Minute << max(0, job.Attempts-1)
	if delay > 10*time.Minute {
		delay = 10 * time.Minute
	}
	status := "retrying"
	var next any = now.Add(delay).Unix()
	var finished any
	if exhausted {
		status = "failed"
		next = nil
		finished = now.Unix()
	}
	message := failure.Error()
	if len(message) > 2000 {
		message = message[:2000]
	}
	_, err = s.db.ExecContext(ctx, `UPDATE appointment_jobs SET status=?, next_attempt_at=?,
		last_error=?, finished_at=?, updated_at=? WHERE id=? AND status='running'`,
		status, next, message, finished, now.Unix(), id)
	return err
}

func (s *Store) Cancel(ctx context.Context, id int64) (bool, error) {
	now := time.Now().Unix()
	return changed(s.db.ExecContext(ctx, `UPDATE appointment_jobs SET status='cancelled', finished_at=?,
		updated_at=?, next_attempt_at=NULL WHERE id=? AND status IN ('pending','retrying')`, now, now, id))
}

func (s *Store) Retry(ctx context.Context, id int64) (bool, error) {
	now := time.Now().Unix()
	return changed(s.db.ExecContext(ctx, `UPDATE appointment_jobs SET status='pending', scheduled_at=?, attempts=0,
		next_attempt_at=NULL, last_error=NULL, result_json=NULL, finished_at=NULL, updated_at=?,
		email_status=NULL, email_error=NULL WHERE id=? AND status IN ('failed','cancelled')`, now, now, id))
}

func (s *Store) Reschedule(ctx context.Context, id int64, scheduledAt time.Time) (bool, error) {
	now := time.Now().Unix()
	return changed(s.db.ExecContext(ctx, `UPDATE appointment_jobs SET scheduled_at=?, status='pending',
		next_attempt_at=NULL, updated_at=? WHERE id=? AND status IN ('pending','retrying')`, scheduledAt.Unix(), now, id))
}

func (s *Store) Delete(ctx context.Context, id int64) (bool, error) {
	return changed(s.db.ExecContext(ctx, `DELETE FROM appointment_jobs WHERE id=? AND status IN ('completed','failed','cancelled')`, id))
}

func (s *Store) SetEmailResult(ctx context.Context, id int64, status string, failure error) error {
	var message any
	if failure != nil {
		text := failure.Error()
		if len(text) > 2000 {
			text = text[:2000]
		}
		message = text
	}
	_, err := s.db.ExecContext(ctx, `UPDATE appointment_jobs SET email_status=?, email_error=?, updated_at=?
		WHERE id=? AND status='completed'`, status, message, time.Now().Unix(), id)
	return err
}

func changed(result sql.Result, err error) (bool, error) {
	if err != nil {
		return false, err
	}
	rows, err := result.RowsAffected()
	return rows == 1, err
}

func (s *Store) SetWorkerHeartbeat(ctx context.Context, now time.Time) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO app_metadata(key,value,updated_at) VALUES('worker_heartbeat',?,?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`,
		fmt.Sprintf("%d", now.Unix()), now.Unix())
	return err
}

func (s *Store) WorkerAlive(ctx context.Context, maxAge time.Duration) (bool, error) {
	var updatedAt int64
	err := s.db.QueryRowContext(ctx, `SELECT updated_at FROM app_metadata WHERE key='worker_heartbeat'`).Scan(&updatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return time.Since(time.Unix(updatedAt, 0)) <= maxAge, nil
}
