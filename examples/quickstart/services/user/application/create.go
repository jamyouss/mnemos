// Package application contains the application services of the user context.
//
// CreateUser is the canonical use case: allocate an ID, build a domain
// User (which performs validation), persist it, and emit a UserCreated
// event for downstream consumers.
package application

import (
	"context"

	"github.com/google/uuid"

	"example.com/quickstart-demo/services/user/domain"
)

// Repository is the storage abstraction. Tests use an in-memory implementation,
// production uses Postgres.
type Repository interface {
	Save(ctx context.Context, u *domain.User) error
}

// EventBus is the outbound event abstraction.
type EventBus interface {
	Publish(ctx context.Context, topic string, payload any) error
}

// CreateUserCommand is the input payload received from the API layer.
type CreateUserCommand struct {
	Email string
	Name  string
}

// CreateUserHandler wires the dependencies needed to handle CreateUserCommand.
type CreateUserHandler struct {
	Repo   Repository
	Events EventBus
}

// Handle runs the use case: validate, persist, emit the UserCreated event.
// Returns the new user's ID on success.
func (h *CreateUserHandler) Handle(ctx context.Context, cmd CreateUserCommand) (string, error) {
	id := uuid.NewString()
	user, err := domain.NewUser(id, cmd.Email, cmd.Name)
	if err != nil {
		return "", err
	}
	if err := h.Repo.Save(ctx, user); err != nil {
		return "", err
	}
	if err := h.Events.Publish(ctx, "user.created", user); err != nil {
		// We still consider creation successful — event publication is at-most-once
		// best-effort and a separate reconciliation job handles retries.
		return id, nil
	}
	return id, nil
}
