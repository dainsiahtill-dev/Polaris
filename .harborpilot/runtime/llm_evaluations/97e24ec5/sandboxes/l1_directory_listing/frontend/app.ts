// Frontend app fixture
// TODO: wire real API integration

export function renderBanner(name: string): string {
  return `Hello, ${name}`;
}

export function hasTodo(value: string): boolean {
  // TODO: replace with typed parser
  return value.includes("TODO");
}
