package ssh

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	cryptossh "golang.org/x/crypto/ssh"
	"golang.org/x/crypto/ssh/agent"
)

func TestUnencryptedIdentityFileWithoutAgent(t *testing.T) {
	_, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	fixture := newConnectionFixture(t, key)
	block, err := cryptossh.MarshalPrivateKey(key, "test")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "id")
	if err := os.WriteFile(path, pem.EncodeToMemory(block), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cfg, err := resolveConfig(ctx, fixture.options)
	if err != nil {
		t.Fatal(err)
	}
	cfg.values["identityfile"] = []string{path}
	cfg.values["identityagent"] = []string{"none"}
	cfg.values["identitiesonly"] = []string{"yes"}
	client, err := cfg.handshake(ctx, 0, &authenticationBudgets{})
	if err != nil {
		t.Fatal(err)
	}
	_ = client.Close()
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, conn, err := cfg.authentication(ctx); err == nil {
		if conn != nil {
			_ = conn.Close()
		}
		t.Fatal("accepted a private key readable by other users")
	}
}

func TestAgentAuthenticationPolicies(t *testing.T) {
	private, err := rsa.GenerateKey(rand.Reader, 1024)
	if err != nil {
		t.Fatal(err)
	}
	signer, err := cryptossh.NewSignerFromKey(private)
	if err != nil {
		t.Fatal(err)
	}
	certificate := &cryptossh.Certificate{Key: signer.PublicKey(), CertType: cryptossh.UserCert, ValidPrincipals: []string{"fixture"}, ValidBefore: cryptossh.CertTimeInfinity}
	if err := certificate.SignCert(rand.Reader, signer); err != nil {
		t.Fatal(err)
	}
	for _, identity := range []any{private, agent.AddedKey{PrivateKey: private, Certificate: certificate}} {
		fixture := newConnectionFixture(t, identity)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		cfg, err := resolveConfig(ctx, fixture.options)
		if err != nil {
			t.Fatal(err)
		}
		cfg.values["identityfile"] = []string{"none"}
		cfg.values["identitiesonly"] = []string{"no"}
		cfg.values["requiredrsasize"] = []string{"2048"}
		if _, conn, err := cfg.authentication(ctx); err == nil {
			conn.Close()
			t.Fatal("undersized agent RSA key accepted")
		}
		cfg.values["requiredrsasize"] = []string{"1024"}
		cfg.values["preferredauthentications"] = []string{"password,keyboard-interactive"}
		if _, conn, err := cfg.authentication(ctx); err == nil || !strings.Contains(err.Error(), "excludes publickey") {
			if conn != nil {
				conn.Close()
			}
			t.Fatalf("public-key exclusion ignored: %v", err)
		}
		cfg.values["preferredauthentications"] = []string{"publickey"}
		address := cfg.get("identityagent")
		cfg.options.Environment = append(cfg.options.Environment, "CUSTOM_SSH_AGENT="+address)
		cfg.values["identityagent"] = []string{"$CUSTOM_SSH_AGENT"}
		_, conn, err := cfg.authentication(ctx)
		if err != nil {
			t.Fatal(err)
		}
		conn.Close()
		cfg.values["identityagent"] = []string{"$MISSING_SSH_AGENT"}
		if _, conn, err := cfg.authentication(ctx); err == nil || !strings.Contains(err.Error(), "unset or empty") {
			if conn != nil {
				conn.Close()
			}
			t.Fatalf("missing named agent variable not rejected: %v", err)
		}
	}
}
