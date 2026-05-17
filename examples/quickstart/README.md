# quickstart-demo

A tiny example Go project Mnemos ships so a first-time user can verify their
setup end-to-end (`make demo`) without having to plug in their own code first.

```
quickstart-demo/
├── README.md
├── services/
│   └── user/
│       ├── domain/user.go         <- User type + invariants
│       └── application/create.go  <- "Create user" use case
└── infra/
    └── logging.go                 <- structured logger setup
```

Once indexed (project name: `quickstart-demo`), try:

```bash
mnemos search-code --project quickstart-demo "how is a user created?"
mnemos search-code --project quickstart-demo "where is logging configured?"
```
