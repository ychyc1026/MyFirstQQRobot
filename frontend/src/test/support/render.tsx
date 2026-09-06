import { render, type RenderOptions } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement, ReactNode } from "react";

function withRouter(ui: ReactElement, initialEntries: string[] = ["/"]) {
  return <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>;
}

export function renderWithRouter(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper"> & { initialEntries?: string[] },
) {
  const { initialEntries = ["/"], ...rest } = options ?? {};
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => withRouter(<>{children}</>, initialEntries),
    ...rest,
  });
}
