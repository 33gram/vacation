import "@testing-library/jest-dom/vitest";

// jsdom не реализует matchMedia — нужен для адаптивной сетки AntD (useBreakpoint).
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
