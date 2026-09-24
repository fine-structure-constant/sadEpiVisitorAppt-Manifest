package automation

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"time"

	"pku-appointment/internal/config"
	"pku-appointment/internal/mailer"
	"pku-appointment/internal/model"
	"pku-appointment/internal/pku"
	"pku-appointment/internal/store"
)

var (
	idPattern    = regexp.MustCompile(`^[a-zA-Z0-9]{6,18}$`)
	phonePattern = regexp.MustCompile(`^\d{11}$`)
)

type Service struct {
	Loader config.Loader
	PKU    *pku.Service
	Store  *store.Store
}

func New(loader config.Loader, pkuService *pku.Service, queue *store.Store) *Service {
	return &Service{Loader: loader, PKU: pkuService, Store: queue}
}

func ValidateAppointment(value model.Appointment) error {
	required := []struct{ value, message string }{
		{value.ContactPhone, "请选择或填写预约人联系电话"},
		{value.Date, "请选择预约日期"},
		{value.Time, "请选择预约时间"},
		{value.Gate, "请选择预约校门"},
		{value.Reason, "请输入预约事由"},
		{value.VisitorName, "请输入被预约人姓名"},
		{value.VisitorID, "请输入被预约人证件号"},
		{value.VisitorPhone, "请输入被预约人联系电话"},
	}
	for _, item := range required {
		if strings.TrimSpace(item.value) == "" {
			return errors.New(item.message)
		}
	}
	if !idPattern.MatchString(value.VisitorID) {
		return errors.New("被预约人证件号应为 6—18 位字母或数字")
	}
	if !phonePattern.MatchString(value.VisitorPhone) {
		return errors.New("被预约人联系电话应为 11 位手机号")
	}
	return nil
}

func AppointmentFromRow(row map[string]any) (model.Appointment, error) {
	appointment := model.Appointment{
		ContactPhone: pku.String(row["lxdh"]),
		Date:         pku.String(row["yyrq"]),
		Time:         pku.String(row["yysj"]),
		Gate:         pku.String(row["yyxm"]),
		Reason:       pku.String(row["yysy"]),
		VisitorName:  pku.String(row["byyrxm"]),
		VisitorID:    pku.String(row["byyrzjh"]),
		VisitorPhone: pku.String(row["byyrlxdh"]),
	}
	if appointment.VisitorID == "" {
		appointment.VisitorID = pku.String(row["zjh"])
	}
	return appointment, ValidateAppointment(appointment)
}

func (s *Service) SaveDraft(ctx context.Context, upstream *pku.Upstream, appointment model.Appointment) (string, map[string]any, error) {
	if err := ValidateAppointment(appointment); err != nil {
		return "", nil, err
	}
	result, err := s.PKU.AppointmentRequest(ctx, upstream, http.MethodPost, "saveSqxx", nil, appointment.AsMap())
	if err != nil {
		return "", nil, err
	}
	row := result["row"]
	remoteID := ""
	if rowMap, ok := row.(map[string]any); ok {
		remoteID = firstNonEmpty(pku.String(rowMap["sqxxid"]), pku.String(rowMap["id"]))
	} else {
		remoteID = pku.String(row)
	}
	if remoteID == "" {
		return "", nil, errors.New("预约已保存，但学校系统没有返回申请编号")
	}
	return remoteID, result, nil
}

func (s *Service) SubmitExisting(ctx context.Context, upstream *pku.Upstream, remoteID string) (map[string]any, error) {
	if strings.TrimSpace(remoteID) == "" {
		return nil, errors.New("预约申请编号不能为空")
	}
	detail, err := s.PKU.AppointmentRequest(ctx, upstream, http.MethodGet, "getSqxx", map[string]string{"sqxxid": remoteID}, nil)
	if err != nil {
		return nil, err
	}
	if strings.EqualFold(pku.String(pku.Row(detail)["tjbz"]), "y") {
		return map[string]any{"reconciled": true, "submitted": detail}, nil
	}
	verification, err := s.PKU.AppointmentRequest(ctx, upstream, http.MethodGet, "sendEcyzCode", map[string]string{"sqxxid": remoteID}, nil)
	if err != nil {
		return nil, err
	}
	row := pku.Row(verification)
	verificationType := strings.ToLower(pku.String(row["type"]))
	code := ""
	switch verificationType {
	case "byz":
	case "opt", "otp":
		if flag, exists := row["flag"]; exists && !pku.Bool(flag) {
			return nil, errors.New(firstNonEmpty(pku.String(row["errmsg"]), "预约令牌验证初始化失败"))
		}
		cfg, err := s.Loader.Load()
		if err != nil {
			return nil, err
		}
		code, err = pku.GenerateTOTP(cfg.TOTPSecret, time.Now())
		if err != nil {
			return nil, err
		}
	default:
		description := map[string]string{"sms": "短信验证码", "wx": "微信验证码"}[verificationType]
		if description == "" {
			description = firstNonEmpty(verificationType, "未知验证")
		}
		return nil, fmt.Errorf("当前预约要求%s，无法自动填写", description)
	}
	submitted, err := s.PKU.AppointmentRequest(ctx, upstream, http.MethodGet, "submitSqxx", map[string]string{"sqxxid": remoteID, "code": code}, nil)
	if err != nil {
		return nil, err
	}
	return map[string]any{"verification_type": verificationType, "submitted": submitted}, nil
}

func (s *Service) ProcessJob(ctx context.Context, job model.Job) {
	upstream, err := s.PKU.LoginAutomatic(ctx)
	if err == nil {
		var result map[string]any
		result, err = s.SubmitExisting(ctx, upstream, job.RemoteID)
		if err == nil {
			err = s.Store.Complete(ctx, job.ID, result)
		}
	}
	if err != nil {
		_ = s.Store.Fail(ctx, job.ID, err)
		return
	}
	_ = s.NotifyCompleted(ctx, job.ID)
}

func (s *Service) NotifyCompleted(ctx context.Context, jobID int64) error {
	job, err := s.Store.Get(ctx, jobID)
	if err != nil {
		return err
	}
	if job.Status != "completed" {
		return errors.New("只有已完成任务可以发送成功通知")
	}
	cfg, err := s.Loader.Load()
	if err != nil {
		return err
	}
	if err := cfg.SMTPReady(); err != nil {
		_ = s.Store.SetEmailResult(ctx, jobID, "skipped", err)
		return err
	}
	if err := mailer.SendSuccess(ctx, cfg.SMTP, *job); err != nil {
		_ = s.Store.SetEmailResult(ctx, jobID, "failed", err)
		return err
	}
	return s.Store.SetEmailResult(ctx, jobID, "sent", nil)
}

func (s *Service) RunWorker(ctx context.Context) error {
	heartbeat := time.NewTicker(5 * time.Second)
	poll := time.NewTicker(time.Second)
	defer heartbeat.Stop()
	defer poll.Stop()
	_ = s.Store.SetWorkerHeartbeat(ctx, time.Now())
	workerDone := make(chan error, 1)
	go func() {
		for {
			job, err := s.Store.ClaimDue(ctx, time.Now())
			if err != nil {
				workerDone <- err
				return
			}
			if job != nil {
				s.ProcessJob(ctx, *job)
				continue
			}
			select {
			case <-ctx.Done():
				workerDone <- ctx.Err()
				return
			case <-poll.C:
			}
		}
	}()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case now := <-heartbeat.C:
			_ = s.Store.SetWorkerHeartbeat(ctx, now)
		case err := <-workerDone:
			return err
		}
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}
