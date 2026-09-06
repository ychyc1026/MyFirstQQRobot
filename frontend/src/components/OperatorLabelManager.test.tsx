import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorLabelManager } from "./OperatorLabelManager";

Element.prototype.hasPointerCapture ||= () => false;
Element.prototype.setPointerCapture ||= () => undefined;
Element.prototype.releasePointerCapture ||= () => undefined;
HTMLElement.prototype.scrollIntoView ||= () => undefined;

vi.mock("../lib/api", () => ({
  api: {
    operatorLabels: vi.fn(),
    setOperatorLabel: vi.fn(),
  },
}));

import { api } from "../lib/api";

describe("OperatorLabelManager", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  beforeEach(() => {
    vi.mocked(api.operatorLabels).mockResolvedValue({
      items: [
        {
          subject_kind: "user",
          subject_id: "2000000003",
          label: "小明",
          updated_at: "2026-09-06T10:00:00+00:00",
        },
      ],
    });
    vi.mocked(api.setOperatorLabel).mockResolvedValue({
      status: "completed",
      data: {},
    } as Awaited<ReturnType<typeof api.setOperatorLabel>>);
  });

  it("lists existing remarks with the name above the number", async () => {
    render(<OperatorLabelManager />);

    expect(await screen.findByText("小明")).toBeInTheDocument();
    expect(screen.getByText("用户 2000000003")).toBeInTheDocument();
    expect(screen.getByText("已备注 1 个")).toBeInTheDocument();
  });

  it("saves a group remark, which is the only dashboard place that can", async () => {
    render(<OperatorLabelManager />);
    await screen.findByText("小明");

    await userEvent.click(screen.getByRole("tab", { name: "群" }));

    fireEvent.change(await screen.findByPlaceholderText("群号"), {
      target: { value: "2000000004" },
    });
    fireEvent.change(screen.getByPlaceholderText("备注名，例如 小明"), {
      target: { value: "测试群" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.setOperatorLabel).toHaveBeenCalled());
    expect(api.setOperatorLabel).toHaveBeenCalledWith("group", "2000000004", "测试群");
  });

  it("rejects a non-numeric target without calling the backend", async () => {
    render(<OperatorLabelManager />);
    await screen.findByText("小明");

    await userEvent.type(screen.getByPlaceholderText("用户 QQ"), "小明");
    await userEvent.type(screen.getByPlaceholderText("备注名，例如 小明"), "小明");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("QQ 只接受数字")).toBeInTheDocument();
    expect(api.setOperatorLabel).not.toHaveBeenCalled();
  });

  it("clears a remark by sending an empty label", async () => {
    render(<OperatorLabelManager />);
    await screen.findByText("小明");

    await userEvent.click(screen.getByRole("button", { name: "清除" }));

    await waitFor(() =>
      expect(api.setOperatorLabel).toHaveBeenCalledWith("user", "2000000003", ""),
    );
  });
});
