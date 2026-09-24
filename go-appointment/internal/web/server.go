package web

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"pku-appointment/internal/automation"
	"pku-appointment/internal/config"
	"pku-appointment/internal/mailer"
	"pku-appointment/internal/model"
	"pku-appointment/internal/pku"
	"pku-appointment/internal/store"
)

//go:embed assets/* assets/static/*
var assets embed.FS

var beijing = time.FixedZone("Asia/Shanghai", 8*60*60)

type Server struct {
	Loader     config.Loader
	PKU        *pku.Service
	Store      *store.Store
	Automation *automation.Service
	cacheMu    sync.Mutex
	cached     *pku.Upstream
}

func New(loader config.Loader, pkuService *pku.Service, queue *store.Store, auto *automation.Service) *Server {
	return &Server{Loader: loader, PKU: pkuService, Store: queue, Automation: auto}
}

func (s *Server) Handler() (http.Handler, error) {
	mux := http.NewServeMux()
	staticFS, err := fs.Sub(assets, "assets/static")
	if err != nil {
		return nil, err
	}
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.FS(staticFS))))
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		http.Redirect(w, r, "/stack", http.StatusFound)
	})
	for route, file := range map[string]string{
		"/topt": "totp.html", "/history": "history.html", "/appointment": "appointment.html", "/stack": "stack.html",
	} {
		path, name := route, file
		mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) { s.servePage(w, name) })
	}
	mux.HandleFunc("GET /totp", func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, "/topt", http.StatusFound) })

	mux.HandleFunc("GET /api/app-status", s.appStatus)
	mux.HandleFunc("GET /api/totp", s.totp)
	mux.HandleFunc("GET /api/history", s.history)
	mux.HandleFunc("GET /api/appointment/config", s.appointmentConfig)
	mux.HandleFunc("GET /api/appointment/check-date", s.checkDate)
	mux.HandleFunc("GET /api/appointment/detail", s.appointmentDetail)
	mux.HandleFunc("POST /api/appointment/save", s.saveAppointment)
	mux.HandleFunc("POST /api/appointment/submit-automatic", s.submitAutomatic)
	mux.HandleFunc("GET /api/stack", s.stackList)
	mux.HandleFunc("POST /api/stack/from-history", s.stackFromHistory)
	mux.HandleFunc("PATCH /api/stack/{id}", s.stackReschedule)
	mux.HandleFunc("POST /api/stack/{id}/cancel", s.stackCancel)
	mux.HandleFunc("POST /api/stack/{id}/retry", s.stackRetry)
	mux.HandleFunc("POST /api/stack/{id}/email", s.stackEmail)
	mux.HandleFunc("DELETE /api/stack/{id}", s.stackDelete)
	return securityHeaders(mux), nil
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		next.ServeHTTP(w, r)
	})
}

func (s *Server) servePage(w http.ResponseWriter, name string) {
	data, err := assets.ReadFile("assets/" + name)
	if err != nil {
		http.Error(w, "页面资源不可用", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(data)
}

func (s *Server) upstream(ctx context.Context) (*pku.Upstream, error) {
	s.cacheMu.Lock()
	defer s.cacheMu.Unlock()
	if s.cached != nil && time.Since(s.cached.CreatedAt) < 15*time.Minute {
		return s.cached, nil
	}
	upstream, err := s.PKU.LoginAutomatic(ctx)
	if err != nil {
		return nil, err
	}
	s.cached = upstream
	return upstream, nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, err error) {
	writeJSON(w, status, map[string]string{"error": err.Error()})
}

func decodeJSON(r *http.Request, value any) error {
	decoder := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	decoder.DisallowUnknownFields()
	return decoder.Decode(value)
}

func (s *Server) appStatus(w http.ResponseWriter, r *http.Request) {
	cfg, err := s.Loader.Load()
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, err)
		return
	}
	accountErr, totpErr, smtpErr := cfg.AccountReady(), cfg.TOTPReady(), cfg.SMTPReady()
	counts, countErr := s.Store.Counts(r.Context())
	alive, heartbeatErr := s.Store.WorkerAlive(r.Context(), 15*time.Second)
	if countErr != nil || heartbeatErr != nil {
		writeError(w, http.StatusInternalServerError, firstError(countErr, heartbeatErr))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"account_configured": accountErr == nil, "account_error": errorText(accountErr),
		"totp_configured": totpErr == nil, "totp_error": errorText(totpErr),
		"smtp_configured": smtpErr == nil, "smtp_error": errorText(smtpErr),
		"scheduler_running": alive, "counts": counts, "timezone": "Asia/Shanghai",
	})
}

func (s *Server) totp(w http.ResponseWriter, r *http.Request) {
	cfg, err := s.Loader.Load()
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, err)
		return
	}
	now := time.Now()
	code, err := pku.GenerateTOTP(cfg.TOTPSecret, now)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, err)
		return
	}
	validUntil := (now.Unix()/30 + 1) * 30
	writeJSON(w, http.StatusOK, map[string]any{"code": code, "digits": 6, "period": 30, "server_time": float64(now.UnixNano()) / 1e9, "valid_until": validUntil})
}

func (s *Server) history(w http.ResponseWriter, r *http.Request) {
	page := boundedInt(r.URL.Query().Get("page"), 1, 1, 1_000_000)
	pageSize := boundedInt(r.URL.Query().Get("pageSize"), 10, 1, 50)
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, fmt.Errorf("自动登录失败：%w", err))
		return
	}
	result, err := s.PKU.History(r.Context(), upstream, page, pageSize)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) appointmentConfig(w http.ResponseWriter, r *http.Request) {
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, err)
		return
	}
	status, err := s.PKU.AppointmentRequest(r.Context(), upstream, http.MethodGet, "getSqzt", map[string]string{"flag": ""}, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	contacts, err := s.PKU.AppointmentRequest(r.Context(), upstream, http.MethodGet, "getJqyyrList", nil, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": status, "contacts": contacts})
}

func (s *Server) checkDate(w http.ResponseWriter, r *http.Request) {
	date := strings.TrimSpace(r.URL.Query().Get("date"))
	if date == "" {
		writeError(w, http.StatusBadRequest, errors.New("请选择预约日期"))
		return
	}
	s.proxyAppointment(w, r, "checkSqrq", map[string]string{"sqrq": date})
}

func (s *Server) appointmentDetail(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.URL.Query().Get("sqxxid"))
	if id == "" {
		writeError(w, http.StatusBadRequest, errors.New("缺少申请编号"))
		return
	}
	s.proxyAppointment(w, r, "getSqxx", map[string]string{"sqxxid": id})
}

func (s *Server) proxyAppointment(w http.ResponseWriter, r *http.Request, action string, params map[string]string) {
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, err)
		return
	}
	result, err := s.PKU.AppointmentRequest(r.Context(), upstream, http.MethodGet, action, params, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) saveAppointment(w http.ResponseWriter, r *http.Request) {
	var appointment model.Appointment
	if err := decodeJSON(r, &appointment); err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, err)
		return
	}
	remoteID, result, err := s.Automation.SaveDraft(r.Context(), upstream, appointment)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	if _, ok := result["row"]; !ok {
		result["row"] = map[string]any{"sqxxid": remoteID}
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) submitAutomatic(w http.ResponseWriter, r *http.Request) {
	var body struct {
		RemoteID string `json:"sqxxid"`
	}
	if err := decodeJSON(r, &body); err != nil || strings.TrimSpace(body.RemoteID) == "" {
		writeError(w, http.StatusBadRequest, errors.New("请先保存预约申请"))
		return
	}
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, err)
		return
	}
	detail, err := s.PKU.AppointmentRequest(r.Context(), upstream, http.MethodGet, "getSqxx", map[string]string{"sqxxid": body.RemoteID}, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	appointment, err := automation.AppointmentFromRow(pku.Row(detail))
	if err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	result, err := s.Automation.SubmitExisting(r.Context(), upstream, body.RemoteID)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	emailStatus, emailError := "skipped", ""
	cfg, configErr := s.Loader.Load()
	if configErr == nil && cfg.SMTPReady() == nil {
		mailJob := model.Job{Appointment: appointment, RemoteID: body.RemoteID}
		if err := mailer.SendSuccess(r.Context(), cfg.SMTP, mailJob); err != nil {
			emailStatus, emailError = "failed", err.Error()
		} else {
			emailStatus = "sent"
		}
	} else if configErr != nil {
		emailError = configErr.Error()
	} else {
		emailError = cfg.SMTPReady().Error()
	}
	writeJSON(w, http.StatusOK, map[string]any{"sqxxid": body.RemoteID, "result": result, "email_status": emailStatus, "email_error": emailError})
}

func (s *Server) stackList(w http.ResponseWriter, r *http.Request) {
	jobs, err := s.Store.List(r.Context(), 200)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"jobs": jobs})
}

func (s *Server) stackFromHistory(w http.ResponseWriter, r *http.Request) {
	var body struct {
		RemoteID    string `json:"sqxxid"`
		ScheduledAt string `json:"scheduled_at"`
		MaxAttempts int    `json:"max_attempts"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	if strings.TrimSpace(body.RemoteID) == "" {
		writeError(w, http.StatusBadRequest, errors.New("请选择一条历史预约草稿"))
		return
	}
	if existing, err := s.Store.ActiveForRemote(r.Context(), body.RemoteID); err == nil {
		writeError(w, http.StatusConflict, fmt.Errorf("该申请已在任务 #%d 中等待提交", existing.ID))
		return
	} else if !errors.Is(err, store.ErrNotFound) {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	scheduledAt, err := parseBeijing(body.ScheduledAt)
	if err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	upstream, err := s.upstream(r.Context())
	if err != nil {
		writeError(w, http.StatusUnauthorized, err)
		return
	}
	detail, err := s.PKU.AppointmentRequest(r.Context(), upstream, http.MethodGet, "getSqxx", map[string]string{"sqxxid": body.RemoteID}, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	row := pku.Row(detail)
	if strings.EqualFold(pku.String(row["tjbz"]), "y") {
		writeError(w, http.StatusConflict, errors.New("该预约已经提交，不能再次加入队列"))
		return
	}
	appointment, err := automation.AppointmentFromRow(row)
	if err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	job, err := s.Store.AddExisting(r.Context(), appointment, body.RemoteID, scheduledAt, body.MaxAttempts)
	if err != nil {
		writeError(w, http.StatusConflict, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"job": job})
}

func (s *Server) stackReschedule(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	var body struct {
		ScheduledAt string `json:"scheduled_at"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	scheduledAt, err := parseBeijing(body.ScheduledAt)
	if err != nil {
		writeError(w, http.StatusBadRequest, err)
		return
	}
	changed, err := s.Store.Reschedule(r.Context(), id, scheduledAt)
	s.writeMutation(w, r, id, changed, err, "只有等待或重试中的任务可以改期")
}

func (s *Server) stackCancel(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	changed, err := s.Store.Cancel(r.Context(), id)
	s.writeMutation(w, r, id, changed, err, "任务不存在，或当前状态不能取消")
}

func (s *Server) stackRetry(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	changed, err := s.Store.Retry(r.Context(), id)
	s.writeMutation(w, r, id, changed, err, "只有失败或已取消的任务可以重试")
}

func (s *Server) stackDelete(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	changed, err := s.Store.Delete(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	if !changed {
		writeError(w, http.StatusConflict, errors.New("只能删除已完成、失败或已取消的任务"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

func (s *Server) stackEmail(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	if err := s.Automation.NotifyCompleted(r.Context(), id); err != nil {
		writeError(w, http.StatusBadGateway, err)
		return
	}
	job, err := s.Store.Get(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"job": job})
}

func (s *Server) writeMutation(w http.ResponseWriter, r *http.Request, id int64, changed bool, err error, conflict string) {
	if err != nil {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	if !changed {
		writeError(w, http.StatusConflict, errors.New(conflict))
		return
	}
	job, err := s.Store.Get(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"job": job})
}

func jobID(w http.ResponseWriter, r *http.Request) (int64, bool) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id < 1 {
		writeError(w, http.StatusBadRequest, errors.New("任务编号无效"))
		return 0, false
	}
	return id, true
}

func parseBeijing(value string) (time.Time, error) {
	if strings.TrimSpace(value) == "" {
		return time.Time{}, errors.New("请选择自动提交时间")
	}
	for _, layout := range []string{"2006-01-02T15:04:05", "2006-01-02T15:04"} {
		if parsed, err := time.ParseInLocation(layout, value, beijing); err == nil {
			return parsed, nil
		}
	}
	if parsed, err := time.Parse(time.RFC3339, value); err == nil {
		return parsed.In(beijing), nil
	}
	return time.Time{}, errors.New("自动提交时间格式无效")
}

func boundedInt(raw string, fallback, minimum, maximum int) int {
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func errorText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
func firstError(errors ...error) error {
	for _, err := range errors {
		if err != nil {
			return err
		}
	}
	return nil
}
