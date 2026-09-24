package mailer

import (
	"bufio"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"mime"
	"net"
	"net/smtp"
	"strings"
	"time"

	"pku-appointment/internal/config"
	"pku-appointment/internal/model"
)

func SendSuccess(ctx context.Context, cfg config.SMTP, job model.Job) error {
	if err := validateAddress(cfg.From); err != nil {
		return fmt.Errorf("发件地址: %w", err)
	}
	if err := validateAddress(cfg.To); err != nil {
		return fmt.Errorf("收件地址: %w", err)
	}
	address := net.JoinHostPort(cfg.Host, fmt.Sprint(cfg.Port))
	dialer := &net.Dialer{Timeout: 30 * time.Second}
	var client *smtp.Client
	if cfg.Port == 465 {
		connection, err := tls.DialWithDialer(dialer, "tcp", address, &tls.Config{ServerName: cfg.Host, MinVersion: tls.VersionTLS12})
		if err != nil {
			return err
		}
		client, err = smtp.NewClient(connection, cfg.Host)
		if err != nil {
			connection.Close()
			return err
		}
	} else {
		connection, err := dialer.DialContext(ctx, "tcp", address)
		if err != nil {
			return err
		}
		client, err = smtp.NewClient(connection, cfg.Host)
		if err != nil {
			connection.Close()
			return err
		}
		if cfg.UseStartTLS() {
			if err := client.StartTLS(&tls.Config{ServerName: cfg.Host, MinVersion: tls.VersionTLS12}); err != nil {
				client.Close()
				return err
			}
		}
	}
	defer client.Close()
	if err := client.Auth(smtp.PlainAuth("", cfg.Username, cfg.Password, cfg.Host)); err != nil {
		return err
	}
	if err := client.Mail(cfg.From); err != nil {
		return err
	}
	if err := client.Rcpt(cfg.To); err != nil {
		return err
	}
	writer, err := client.Data()
	if err != nil {
		return err
	}
	buffer := bufio.NewWriter(writer)
	subject := mime.QEncoding.Encode("UTF-8", "访客预约提交成功："+job.Appointment.VisitorName)
	body := strings.Join([]string{
		"自动预约机已成功提交预约。", "",
		fmt.Sprintf("队列编号：%d", job.ID),
		"申请编号：" + job.RemoteID,
		"被预约人：" + job.Appointment.VisitorName,
		"预约日期：" + job.Appointment.Date,
		"预约时间：" + job.Appointment.Time,
		"预约校门：" + job.Appointment.Gate,
		"预约事由：" + job.Appointment.Reason,
		"", "此邮件由自动预约机发送。",
	}, "\r\n")
	message := fmt.Sprintf("From: %s\r\nTo: %s\r\nSubject: %s\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n%s\r\n", cfg.From, cfg.To, subject, body)
	if _, err := buffer.WriteString(message); err != nil {
		writer.Close()
		return err
	}
	if err := buffer.Flush(); err != nil {
		writer.Close()
		return err
	}
	if err := writer.Close(); err != nil {
		return err
	}
	return client.Quit()
}

func validateAddress(value string) error {
	if value == "" {
		return errors.New("不能为空")
	}
	if strings.ContainsAny(value, "\r\n") {
		return errors.New("包含非法换行")
	}
	return nil
}
