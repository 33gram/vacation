import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoadingState } from "./states";

// `ui.md` §4: «Загрузка» - каркас, не крутилка. Здесь проверяется ЧИСЛО элементов каркаса по
// форме; высота в пикселях не проверяется (в jsdom нет раскладки), см. докстринг компонента.
describe("LoadingState shapes (ui.md §4)", () => {
  it("table: the named number of rows, each with the named number of cells", () => {
    const { container } = render(<LoadingState shape={{ kind: "table", rows: 7, columns: 3 }} />);

    const rows = container.querySelectorAll("[data-skeleton-row]");
    expect(rows).toHaveLength(7);
    rows.forEach((row) => {
      expect(row.querySelectorAll("[data-skeleton-cell]")).toHaveLength(3);
    });
  });

  it("card: the named number of blocks", () => {
    const { container } = render(<LoadingState shape={{ kind: "card", blocks: 4 }} />);

    expect(container.querySelectorAll("[data-skeleton-block]")).toHaveLength(4);
  });

  it("form: the named number of fields", () => {
    const { container } = render(<LoadingState shape={{ kind: "form", fields: 9 }} />);

    expect(container.querySelectorAll("[data-skeleton-field]")).toHaveLength(9);
  });

  it("each shape draws only its own kind of element", () => {
    const table = render(<LoadingState shape={{ kind: "table", rows: 2, columns: 2 }} />);
    expect(table.container.querySelector("[data-skeleton-field], [data-skeleton-block]")).toBeNull();
    table.unmount();

    const form = render(<LoadingState shape={{ kind: "form", fields: 2 }} />);
    expect(form.container.querySelector("[data-skeleton-row], [data-skeleton-block]")).toBeNull();
    form.unmount();

    const card = render(<LoadingState shape={{ kind: "card", blocks: 2 }} />);
    expect(card.container.querySelector("[data-skeleton-row], [data-skeleton-field]")).toBeNull();
  });

  it("is a skeleton marked busy, never a spinner, in every shape", () => {
    const shapes = [
      { kind: "table", rows: 1, columns: 1 },
      { kind: "card", blocks: 1 },
      { kind: "form", fields: 1 },
    ] as const;
    for (const shape of shapes) {
      const { container, unmount } = render(<LoadingState shape={shape} />);
      expect(container.querySelector(".ant-skeleton")).not.toBeNull();
      expect(container.querySelector(".ant-spin")).toBeNull();
      expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
      unmount();
    }
  });

  it("scales with the shape: a bigger shape draws more elements, not a fixed four", () => {
    const small = render(<LoadingState shape={{ kind: "table", rows: 2, columns: 2 }} />);
    const smallCount = small.container.querySelectorAll("[data-skeleton-row]").length;
    small.unmount();
    const large = render(<LoadingState shape={{ kind: "table", rows: 12, columns: 2 }} />);

    expect(smallCount).toBe(2);
    expect(large.container.querySelectorAll("[data-skeleton-row]")).toHaveLength(12);
  });
});
