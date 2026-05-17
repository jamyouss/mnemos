// Package domain defines the core User aggregate.
//
// A User is identified by an immutable ID and an email address that's
// required to be unique across the system. Domain invariants are enforced
// at construction time — callers cannot bypass NewUser to build an instance.
package domain

import (
	"errors"
	"net/mail"
	"strings"
)

// User is the central aggregate of the user bounded context.
type User struct {
	ID    string
	Email string
	Name  string
}

// ErrInvalidEmail is returned by NewUser when the email cannot be parsed.
var ErrInvalidEmail = errors.New("invalid email address")

// ErrMissingName is returned when the name is empty after trimming.
var ErrMissingName = errors.New("name is required")

// NewUser validates and constructs a User. The caller is responsible for
// allocating the ID (typically a UUID from the application layer).
func NewUser(id, email, name string) (*User, error) {
	email = strings.TrimSpace(email)
	if _, err := mail.ParseAddress(email); err != nil {
		return nil, ErrInvalidEmail
	}
	name = strings.TrimSpace(name)
	if name == "" {
		return nil, ErrMissingName
	}
	return &User{ID: id, Email: email, Name: name}, nil
}
