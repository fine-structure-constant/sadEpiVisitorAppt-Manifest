package model

import "time"

type Appointment struct {
	ContactPhone string `json:"lxdh"`
	Date         string `json:"yyrq"`
	Time         string `json:"yysj"`
	Gate         string `json:"yyxm"`
	Reason       string `json:"yysy"`
	VisitorName  string `json:"byyrxm"`
	VisitorID    string `json:"byyrzjh"`
	VisitorPhone string `json:"byyrlxdh"`
}

type Job struct {
	ID            int64          `json:"id"`
	Appointment   Appointment    `json:"payload"`
	ScheduledAt   time.Time      `json:"scheduled_at"`
	Status        string         `json:"status"`
	Attempts      int            `json:"attempts"`
	MaxAttempts   int            `json:"max_attempts"`
	NextAttemptAt *time.Time     `json:"next_attempt_at"`
	RemoteID      string         `json:"remote_id"`
	Result        map[string]any `json:"result"`
	LastError     string         `json:"last_error"`
	EmailStatus   string         `json:"email_status"`
	EmailError    string         `json:"email_error"`
	CreatedAt     time.Time      `json:"created_at"`
	UpdatedAt     time.Time      `json:"updated_at"`
	StartedAt     *time.Time     `json:"started_at"`
	FinishedAt    *time.Time     `json:"finished_at"`
}

func (a Appointment) AsMap() map[string]string {
	return map[string]string{
		"lxdh":     a.ContactPhone,
		"yyrq":     a.Date,
		"yysj":     a.Time,
		"yyxm":     a.Gate,
		"yysy":     a.Reason,
		"byyrxm":   a.VisitorName,
		"byyrzjh":  a.VisitorID,
		"byyrlxdh": a.VisitorPhone,
	}
}
