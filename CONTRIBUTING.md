# Contributing

## Development Setup

```bash
# Clone the repository
git clone https://github.com/dsaponenko/mcporter-proxy.git
cd mcporter-proxy

# Build
make build

# Test
make test
```

## Making Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests (`make test`)
5. Commit your changes (`git commit -am 'Add new feature'`)
6. Push to the branch (`git push origin feature/my-feature`)
7. Create a Pull Request

## Project Structure

```
mcporter-proxy/
├── server/           # Python HTTP proxy server
├── client/           # Node.js CLI client
├── tests/            # Tests (test_client.js, mock_server.js)
├── Makefile          # Build and test commands
└── docker-compose.fragment  # Docker Compose integration
```

## Style Guidelines

- Python: Follow PEP 8 (use `ruff` for linting)
- JavaScript: Use standard style (ESLint)
- Commit messages: Use clear, descriptive messages
- Tests: Add tests for new features
