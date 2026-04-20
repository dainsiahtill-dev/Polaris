# Polaris Frontend

React-based dashboard for the Polaris agent system.

## Project Structure

- `src/app/` - Main application components and layout
  - `components/` - Reusable UI components
    - `ProjectProgressPanel/` - Task management and progress visualization (Modularized)
    - `DialoguePanel/` - Real-time chat interface
    - `ContextSidebar/` - Document context and memory
- `src/types/` - Shared TypeScript definitions
- `src/utils/` - Utility functions (including performance monitoring)
- `src/test/` - Testing setup and utilities

## Key Features

- **Component Architecture**: Modularized and performance-optimized React components.
- **Performance Monitoring**: Built-in render timing and lifecycle tracking via `utils/performance.tsx`.
- **Type Safety**: Strict TypeScript definitions for core data structures.
- **Testing**: Fully configured Vitest environment.

## Getting Started

### Installation

```bash
npm install
```

### Development

Start the development server (with Electron):

```bash
npm run dev
```

### Testing

Run the test suite:

```bash
# Run unit tests
npm test

# Run tests with UI
npm run test:ui

# Generate coverage report
npm run test:coverage
```

## Performance Optimization

Key components like `ProjectProgressPanel` are optimized using `React.memo` to prevent unnecessary re-renders. Use the `useRenderTime` hook in `src/utils/performance.tsx` to debug performance issues.