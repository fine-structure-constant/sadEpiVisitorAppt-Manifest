package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Account struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type SMTP struct {
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Username string `json:"username"`
	Password string `json:"password"`
	From     string `json:"from"`
	To       string `json:"to"`
	StartTLS *bool  `json:"starttls"`
}

type Server struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

type Network struct {
	UseEnvironmentProxy bool `json:"use_environment_proxy"`
}

type Config struct {
	Server     Server  `json:"server"`
	Account    Account `json:"account"`
	TOTPSecret string  `json:"totp_secret"`
	SMTP       SMTP    `json:"smtp"`
	Network    Network `json:"network"`
}

type Loader struct {
	Path string
}

func (l Loader) Load() (Config, error) {
	data, err := os.ReadFile(l.Path)
	if err != nil {
		return Config{}, fmt.Errorf("读取配置 %s: %w", l.Path, err)
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return Config{}, fmt.Errorf("解析配置 %s: %w", l.Path, err)
	}
	if cfg.Server.Host == "" {
		cfg.Server.Host = "127.0.0.1"
	}
	if cfg.Server.Port == 0 {
		cfg.Server.Port = 8000
	}
	if cfg.SMTP.Port == 0 {
		cfg.SMTP.Port = 587
	}
	if cfg.SMTP.To == "" {
		cfg.SMTP.To = cfg.SMTP.Username
	}
	return cfg, nil
}

func (c Config) ListenAddress() (string, error) {
	if c.Server.Port < 1 || c.Server.Port > 65535 {
		return "", fmt.Errorf("server.port 无效: %d", c.Server.Port)
	}
	return fmt.Sprintf("%s:%d", c.Server.Host, c.Server.Port), nil
}

func (c Config) AccountReady() error {
	if strings.TrimSpace(c.Account.Username) == "" || c.Account.Password == "" {
		return errors.New("config.json 中的账户或密码为空")
	}
	return nil
}

func (c Config) TOTPReady() error {
	if strings.TrimSpace(c.TOTPSecret) == "" {
		return errors.New("尚未配置 TOTP 密钥")
	}
	return nil
}

func (c Config) SMTPReady() error {
	if strings.TrimSpace(c.SMTP.Host) == "" || strings.TrimSpace(c.SMTP.Username) == "" || c.SMTP.Password == "" || strings.TrimSpace(c.SMTP.From) == "" || strings.TrimSpace(c.SMTP.To) == "" {
		return errors.New("config.json 中的 SMTP 配置不完整")
	}
	if c.SMTP.Port < 1 || c.SMTP.Port > 65535 {
		return errors.New("SMTP 端口无效")
	}
	return nil
}

func (s SMTP) UseStartTLS() bool {
	if s.StartTLS == nil {
		return s.Port != 465
	}
	return *s.StartTLS
}

func DefaultConfigPath() (string, error) {
	executable, err := os.Executable()
	if err == nil {
		path := filepath.Join(filepath.Dir(executable), "config.json")
		if _, statErr := os.Stat(path); statErr == nil {
			return path, nil
		}
	}
	workingDirectory, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return filepath.Join(workingDirectory, "config.json"), nil
}

func DefaultDatabasePath() (string, error) {
	workingDirectory, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return filepath.Join(workingDirectory, "appointments.db"), nil
}
