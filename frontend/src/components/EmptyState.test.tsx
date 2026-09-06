import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";
import { LoadingState } from "./LoadingState";

afterEach(() => {
  cleanup();
});

describe("EmptyState", () => {
  it("renders panel tone with title and detail", () => {
    render(<EmptyState title="还没有任务" detail="先创建一个。" />);
    expect(screen.getByRole("status")).toHaveTextContent("还没有任务");
    expect(screen.getByText("先创建一个。")).toBeInTheDocument();
  });

  it("renders inline tone without forcing a dashed panel", () => {
    const { container } = render(
      <EmptyState tone="inline" title="没有匹配的备注或 QQ。" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("没有匹配的备注或 QQ。");
    expect(container.querySelector(".border-dashed")).toBeNull();
  });

  it("renders select tone for unselected detail panes", () => {
    render(<EmptyState tone="select" title="从左侧选一条审批。" />);
    expect(screen.getByRole("status")).toHaveTextContent("从左侧选一条审批。");
  });
});

describe("LoadingState", () => {
  it("exposes a busy status for content-area loading", () => {
    render(<LoadingState rows={2} />);
    const status = screen.getByRole("status", { name: "加载中" });
    expect(status).toHaveAttribute("aria-busy", "true");
  });
});
