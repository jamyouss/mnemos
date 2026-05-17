// Package infra wires technical components shared across services.
//
// NewLogger builds the structured slog.Logger every service uses. The level
// is read from the LOG_LEVEL env var (default "info") and the format
// switches between JSON (production) and plain text (development).
package infra

import (
	"log/slog"
	"os"
)

// NewLogger returns the project-wide logger.
//
// Format selection:
//   - env == "production" → JSON handler (machine-readable, ships to Loki)
//   - everything else      → text handler (human-readable in the terminal)
func NewLogger(env string) *slog.Logger {
	level := slog.LevelInfo
	switch os.Getenv("LOG_LEVEL") {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}

	opts := &slog.HandlerOptions{Level: level}
	if env == "production" {
		return slog.New(slog.NewJSONHandler(os.Stdout, opts))
	}
	return slog.New(slog.NewTextHandler(os.Stdout, opts))
}
