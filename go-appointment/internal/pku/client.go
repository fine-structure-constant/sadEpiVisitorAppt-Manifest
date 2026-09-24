package pku

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"time"

	"github.com/pquerna/otp"
	"github.com/pquerna/otp/totp"

	"pku-appointment/internal/config"
)

const (
	appID          = "portal2017"
	appName        = "北京大学校内信息门户新版"
	redirectURL    = "https://portal.pku.edu.cn/portal2017/ssoLogin.do"
	publicKeyURL   = "https://iaaa.pku.edu.cn/iaaa/getPublicKey.do"
	verifyURL      = "https://iaaa.pku.edu.cn/iaaa/isMobileAuthen.do"
	loginURL       = "https://iaaa.pku.edu.cn/iaaa/oauthlogin.do"
	portletURL     = "https://portal.pku.edu.cn/portal2017/util/portletRedir.do?portletId=sadEpiVisitorAppt"
	simsoLoginURL  = "https://simso.pku.edu.cn/ssapi/simsoLogin"
	appointmentAPI = "https://simso.pku.edu.cn/ssapi/stuaffair/epiVisitorAppt"
	historyURL     = appointmentAPI + "/getSqxxHis"
	userAgent      = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"
)

type Upstream struct {
	HTTP      *http.Client
	StudentID string
	SID       string
	Username  string
	CreatedAt time.Time
}

type Service struct {
	Loader config.Loader
}

func NewService(loader config.Loader) *Service { return &Service{Loader: loader} }

func newHTTPClient(useEnvironmentProxy bool) (*http.Client, error) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if !useEnvironmentProxy {
		transport.Proxy = nil
	}
	return &http.Client{Jar: jar, Transport: transport, Timeout: 35 * time.Second}, nil
}

func oauthURL() string {
	values := url.Values{"appID": {appID}, "appName": {appName}, "redirectUrl": {redirectURL}}
	return "https://iaaa.pku.edu.cn/iaaa/oauth.jsp?" + values.Encode()
}

func randomNumber() string {
	limit := new(big.Int).Lsh(big.NewInt(1), 53)
	value, err := rand.Int(rand.Reader, limit)
	if err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return value.String()
}

func request(ctx context.Context, client *http.Client, method, endpoint string, body io.Reader, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
	req.Header.Set("Cache-Control", "no-cache")
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	response, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 400 {
		defer response.Body.Close()
		limited, _ := io.ReadAll(io.LimitReader(response.Body, 1024))
		return nil, fmt.Errorf("上游 HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(limited)))
	}
	return response, nil
}

func decodeJSON(response *http.Response) (map[string]any, error) {
	defer response.Body.Close()
	var result map[string]any
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprint(value)
}

func boolValue(value any) bool {
	result, _ := value.(bool)
	return result
}

func rowMap(result map[string]any) map[string]any {
	row, _ := result["row"].(map[string]any)
	return row
}

func (s *Service) prepare(ctx context.Context, cfg config.Config) (*http.Client, map[string]any, error) {
	client, err := newHTTPClient(cfg.Network.UseEnvironmentProxy)
	if err != nil {
		return nil, nil, err
	}
	response, err := request(ctx, client, http.MethodGet, oauthURL(), nil, nil)
	if err != nil {
		return nil, nil, err
	}
	response.Body.Close()
	iaaaURL, _ := url.Parse("https://iaaa.pku.edu.cn/")
	client.Jar.SetCookies(iaaaURL, []*http.Cookie{
		{Name: "userName", Value: cfg.Account.Username, Path: "/"},
		{Name: "remember", Value: "true", Path: "/"},
	})
	query := url.Values{"userName": {cfg.Account.Username}, "appId": {appID}, "_rand": {randomNumber()}}
	response, err = request(ctx, client, http.MethodGet, verifyURL+"?"+query.Encode(), nil, nil)
	if err != nil {
		return nil, nil, err
	}
	info, err := decodeJSON(response)
	return client, info, err
}

func encryptPassword(password, pemKey string) (string, error) {
	block, _ := pem.Decode([]byte(pemKey))
	if block == nil {
		return "", errors.New("IAAA 返回的公钥格式无效")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		if key, pkcsErr := x509.ParsePKCS1PublicKey(block.Bytes); pkcsErr == nil {
			parsed = key
		} else {
			return "", err
		}
	}
	publicKey, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return "", errors.New("IAAA 返回的不是 RSA 公钥")
	}
	encrypted, err := rsa.EncryptPKCS1v15(rand.Reader, publicKey, []byte(password))
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(encrypted), nil
}

func (s *Service) LoginAutomatic(ctx context.Context) (*Upstream, error) {
	cfg, err := s.Loader.Load()
	if err != nil {
		return nil, err
	}
	if err := cfg.AccountReady(); err != nil {
		return nil, err
	}
	client, verification, err := s.prepare(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if boolValue(verification["showCode"]) {
		return nil, errors.New("账户登录要求图形验证码，无法自动登录")
	}
	otpCode := ""
	if boolValue(verification["isMobileAuthen"]) {
		mode := strings.ToUpper(stringValue(verification["authenMode"]))
		if mode != "OTP" {
			return nil, fmt.Errorf("账户登录要求 %s 二次验证，无法自动填写", mode)
		}
		otpCode, err = GenerateTOTP(cfg.TOTPSecret, time.Now())
		if err != nil {
			return nil, err
		}
	}
	return s.performLogin(ctx, client, cfg, otpCode)
}

func (s *Service) performLogin(ctx context.Context, client *http.Client, cfg config.Config, otpCode string) (*Upstream, error) {
	response, err := request(ctx, client, http.MethodGet, publicKeyURL, nil, nil)
	if err != nil {
		return nil, err
	}
	keyData, err := decodeJSON(response)
	if err != nil {
		return nil, err
	}
	if !boolValue(keyData["success"]) || stringValue(keyData["key"]) == "" {
		return nil, errors.New("无法获取 IAAA 公钥")
	}
	encryptedPassword, err := encryptPassword(cfg.Account.Password, stringValue(keyData["key"]))
	if err != nil {
		return nil, err
	}
	form := url.Values{
		"appid":       {appID},
		"userName":    {cfg.Account.Username},
		"password":    {encryptedPassword},
		"randCode":    {""},
		"smsCode":     {""},
		"otpCode":     {otpCode},
		"remTrustChk": {"true"},
		"redirUrl":    {redirectURL},
	}
	response, err = request(ctx, client, http.MethodPost, loginURL, strings.NewReader(form.Encode()), map[string]string{
		"Content-Type":     "application/x-www-form-urlencoded",
		"Origin":           "https://iaaa.pku.edu.cn",
		"Referer":          oauthURL(),
		"X-Requested-With": "XMLHttpRequest",
	})
	if err != nil {
		return nil, err
	}
	loginData, err := decodeJSON(response)
	if err != nil {
		return nil, err
	}
	if !boolValue(loginData["success"]) {
		message := "IAAA 登录失败"
		if errorsMap, ok := loginData["errors"].(map[string]any); ok && stringValue(errorsMap["msg"]) != "" {
			message = stringValue(errorsMap["msg"])
		}
		return nil, errors.New(message)
	}
	token := stringValue(loginData["token"])
	if token == "" {
		return nil, errors.New("IAAA 登录响应缺少 token")
	}
	tokenURL := redirectURL + "?_rand=" + randomNumber() + "&token=" + url.QueryEscape(token)
	response, err = request(ctx, client, http.MethodGet, tokenURL, nil, nil)
	if err != nil {
		return nil, err
	}
	response.Body.Close()
	response, err = request(ctx, client, http.MethodGet, portletURL, nil, nil)
	if err != nil {
		return nil, err
	}
	response.Body.Close()
	if response.Request == nil || response.Request.URL.Host != "simso.pku.edu.cn" {
		return nil, errors.New("门户没有跳转到 SIMSO")
	}
	simsoToken := response.Request.URL.Query().Get("token")
	if simsoToken == "" {
		return nil, errors.New("SIMSO 重定向缺少 token")
	}
	query := url.Values{"token": {simsoToken}, "page": {"sadEpiVisitorAppt.html"}}
	response, err = request(ctx, client, http.MethodGet, simsoLoginURL+"?"+query.Encode(), nil, map[string]string{
		"Referer":          response.Request.URL.String(),
		"X-Requested-With": "XMLHttpRequest",
	})
	if err != nil {
		return nil, err
	}
	simsoData, err := decodeJSON(response)
	if err != nil {
		return nil, err
	}
	if !boolValue(simsoData["success"]) {
		return nil, fmt.Errorf("SIMSO 登录失败: %s", stringValue(simsoData["msg"]))
	}
	sid, studentID := stringValue(simsoData["sid"]), stringValue(simsoData["xh"])
	if sid == "" || studentID == "" {
		return nil, errors.New("SIMSO 登录响应缺少 sid 或学号")
	}
	simsoURL, _ := url.Parse("https://simso.pku.edu.cn/")
	client.Jar.SetCookies(simsoURL, []*http.Cookie{{Name: "sid", Value: sid, Path: "/"}})
	finalURL := "https://simso.pku.edu.cn/pages/sadEpiVisitorAppt.html?_sk=" + url.QueryEscape(studentID) + "#/index"
	response, err = request(ctx, client, http.MethodGet, finalURL, nil, nil)
	if err != nil {
		return nil, err
	}
	response.Body.Close()
	return &Upstream{HTTP: client, StudentID: studentID, SID: sid, Username: cfg.Account.Username, CreatedAt: time.Now()}, nil
}

func (s *Service) AppointmentRequest(ctx context.Context, upstream *Upstream, method, action string, params map[string]string, data map[string]string) (map[string]any, error) {
	query := url.Values{"sid": {upstream.SID}, "_sk": {upstream.StudentID}}
	for key, value := range params {
		query.Set(key, value)
	}
	var body io.Reader
	headers := map[string]string{"X-Requested-With": "XMLHttpRequest"}
	if method == http.MethodPost || method == http.MethodPut || method == http.MethodPatch {
		encoded, err := json.Marshal(data)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(encoded)
		headers["Content-Type"] = "application/json"
	}
	response, err := request(ctx, upstream.HTTP, method, appointmentAPI+"/"+action+"?"+query.Encode(), body, headers)
	if err != nil {
		return nil, err
	}
	result, err := decodeJSON(response)
	if err != nil {
		return nil, err
	}
	code := stringValue(result["code"])
	if code != "" && code != "1" && !boolValue(result["success"]) {
		return nil, errors.New(firstNonEmpty(stringValue(result["msg"]), "预约接口返回失败"))
	}
	if success, exists := result["success"]; exists && !boolValue(success) && code != "1" {
		return nil, errors.New(firstNonEmpty(stringValue(result["msg"]), "预约接口返回失败"))
	}
	return result, nil
}

func (s *Service) History(ctx context.Context, upstream *Upstream, page, pageSize int) (map[string]any, error) {
	query := url.Values{
		"sid": {upstream.SID}, "_sk": {upstream.StudentID},
		"pageSize": {fmt.Sprint(pageSize)}, "pageNum": {fmt.Sprint(page)},
	}
	response, err := request(ctx, upstream.HTTP, http.MethodGet, historyURL+"?"+query.Encode(), nil, map[string]string{"X-Requested-With": "XMLHttpRequest"})
	if err != nil {
		return nil, err
	}
	result, err := decodeJSON(response)
	if err != nil {
		return nil, err
	}
	if stringValue(result["code"]) != "1" && !boolValue(result["success"]) {
		return nil, errors.New(firstNonEmpty(stringValue(result["msg"]), "历史记录读取失败"))
	}
	return result, nil
}

func GenerateTOTP(secret string, now time.Time) (string, error) {
	secret = strings.ToUpper(strings.Join(strings.Fields(secret), ""))
	if secret == "" {
		return "", errors.New("尚未配置 TOTP 密钥")
	}
	return totp.GenerateCodeCustom(secret, now, totp.ValidateOpts{
		Period: 30, Skew: 0, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1,
	})
}

func Row(result map[string]any) map[string]any { return rowMap(result) }
func String(value any) string                  { return stringValue(value) }
func Bool(value any) bool                      { return boolValue(value) }

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}
