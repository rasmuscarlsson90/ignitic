package testutil

import (
	"fmt"
	"net/http"
)

// Test handlers run in process and use response recorders. Reject outbound HTTP
// instead of relying on runner-specific proxies to stop email/provider calls.
type blockedHTTPTransport struct{}

func (blockedHTTPTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Body != nil {
		_ = req.Body.Close()
	}
	return nil, fmt.Errorf("outbound HTTP disabled in backend tests: %s %s://%s", req.Method, req.URL.Scheme, req.URL.Host)
}

func init() {
	http.DefaultTransport = blockedHTTPTransport{}
}
