# c4-genai-suite

## Repository structure

This is a monorepo with three main packages:

- `frontend/` — React + Vite + Mantine UI (TypeScript)
- `backend/` — NestJS + TypeORM (TypeScript)
- `e2e/` — Playwright end-to-end tests

Interfaces between frontend and backend are defined via OpenAPI specs. Generated API clients live in `frontend/src/api/generated/` and `backend/src/domain/chat/middlewares/generated/`.

## Commands

Run these from the **repo root**:

```sh
# Run all tests (frontend unit + backend unit + e2e)
npm test

# Run only frontend unit tests
npm run test:frontend    # → cd frontend && vitest run --coverage

# Run only backend unit tests
npm run test:backend     # → cd backend && jest --runInBand --forceExit
```

Lint and build must be run **inside the subpackage**:

```sh
# Frontend
cd frontend && npm run lint:fix && npm run build

# Backend
cd backend && npm run lint:fix && npm run build
```

## Test conventions

- Frontend unit tests: `*.ui-unit.spec.tsx` or `*.unit.spec.tsx` or `*.integration.spec.tsx` — uses vitest
- Backend unit tests: `*.spec.ts` next to source files — uses jest
- E2E tests: `e2e/tests/**/*.spec.ts` — uses Playwright (requires running services, use `npm run test:e2e`)

When adding tests for new features, place them next to the source file following existing naming conventions.

## After implementing

1. Lint and fix: `cd frontend && npm run lint:fix` and `cd backend && npm run lint:fix`
2. Build both: `cd frontend && npm run build` and `cd backend && npm run build`
3. Run tests: `npm test` (from repo root)
4. Fix any remaining lint or type errors manually

## Key directories for chat features

- `frontend/src/pages/chat/` — chat UI components and page
- `frontend/src/pages/chat/conversation/` — ChatInput, ChatHistory, ChatItem components
- `frontend/src/pages/chat/state/` — chat state management (Zustand stores)
- `backend/src/domain/chat/` — chat domain logic, middleware pipeline, use-cases
- `backend/src/domain/chat/use-cases/send-message.ts` — message sending logic
- `backend/src/domain/chat/middlewares/` — request processing middleware chain

## API changes

If you modify backend API endpoints, regenerate the OpenAPI spec and frontend client:

```sh
cd backend && npm run generate-openapi-dev
cd frontend && npm run generate-api
```
