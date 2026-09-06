import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { PageColumn, PageFrame, PageSplit } from "../PageLayout";
import { Select } from "../Select";
import { getStoredTheme, setStoredTheme } from "../../lib/theme";
import { Button } from "./button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "./dialog";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle, AlertDialogTrigger } from "./alert-dialog";
import { Switch } from "./switch";

Element.prototype.hasPointerCapture ||= () => false;
Element.prototype.setPointerCapture ||= () => undefined;
Element.prototype.releasePointerCapture ||= () => undefined;
HTMLElement.prototype.scrollIntoView ||= () => undefined;

describe("shared dashboard primitives", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  it("keeps disabled actions inert", async () => {
    let calls = 0;
    render(<Button disabled onClick={() => calls++}>执行</Button>);
    await userEvent.click(screen.getByRole("button", { name: "执行" }));
    expect(calls).toBe(0);
    expect(screen.getByRole("button", { name: "执行" })).toBeDisabled();
  });

  it("opens and dismisses a keyboard-accessible dialog", async () => {
    render(<Dialog><DialogTrigger asChild><Button>打开</Button></DialogTrigger><DialogContent><DialogTitle>详情</DialogTitle></DialogContent></Dialog>);
    await userEvent.click(screen.getByRole("button", { name: "打开" }));
    expect(screen.getByRole("dialog")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("returns focus after cancelling a destructive confirmation", async () => {
    render(<AlertDialog><AlertDialogTrigger asChild><Button>生成隔离预览</Button></AlertDialogTrigger><AlertDialogContent><AlertDialogTitle>确认移入隔离区</AlertDialogTitle><AlertDialogDescription>不会永久删除。</AlertDialogDescription><AlertDialogCancel>取消</AlertDialogCancel></AlertDialogContent></AlertDialog>);
    const trigger = screen.getByRole("button", { name: "生成隔离预览" });
    await userEvent.click(trigger);
    expect(screen.getByRole("alertdialog")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("supports switch keyboard state and only light/dark themes", async () => {
    render(<Switch aria-label="启用" />);
    const control = screen.getByRole("switch", { name: "启用" });
    control.focus();
    await userEvent.keyboard(" ");
    expect(control).toHaveAttribute("data-state", "checked");
    setStoredTheme("dark");
    expect(getStoredTheme()).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("opens the shared select, chooses an option and dismisses the portal", async () => {
    let selected = "observe";
    const view = render(<Select value={selected} onChange={(next) => { selected = next; }} options={[{ value: "observe", label: "仅观察" }, { value: "shadow", label: "影子运行" }]} aria-label="运行模式" />);
    const trigger = screen.getByRole("combobox", { name: "运行模式" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeVisible();
    fireEvent.click(screen.getByRole("option", { name: "影子运行" }));
    expect(selected).toBe("shadow");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    view.unmount();
  });

  it("keeps page columns on one shared bottom-edge contract", () => {
    const view = render(
      <PageFrame>
        <PageSplit>
          <PageColumn>
            <section>第一张卡片</section>
            <section>底部卡片</section>
          </PageColumn>
          <section>另一列</section>
        </PageSplit>
      </PageFrame>,
    );

    const frame = view.container.querySelector('[data-page-layout="frame"]');
    const split = view.container.querySelector('[data-page-layout="split"]');
    const column = view.container.querySelector('[data-page-layout="column"]');

    expect(frame).toHaveClass("flex", "flex-col");
    expect(split).toHaveClass("grid", "flex-1", "items-stretch");
    expect(column).toHaveClass("flex", "flex-col", "[&>:last-child]:flex-1");
  });
});
