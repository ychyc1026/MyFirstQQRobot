import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QualificationPage } from "./QualificationPage";

const qualificationSuites = vi.fn();
const qualificationRoutes = vi.fn();
const qualificationDecisions = vi.fn();
const qualificationRuns = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    qualificationSuites: () => qualificationSuites(),
    qualificationRoutes: () => qualificationRoutes(),
    qualificationDecisions: () => qualificationDecisions(),
    qualificationRuns: () => qualificationRuns(),
    qualificationRunDetail: vi.fn(),
    previewQualification: vi.fn(),
    confirmQualification: vi.fn(),
    cancelQualificationRun: vi.fn(),
  },
}));

describe("QualificationPage", () => {
  beforeEach(() => {
    qualificationSuites.mockResolvedValue({
      items: [
        {
          suite_id: "chat-shadow-v1",
          capability: "chat",
          version: "2026.09.05",
          fixture_ids: ["chat-identity-001"],
          fixture_count: 1,
          blocking_check_codes: ["creator_identity"],
          source_kind: "repository_owned",
          sensitivity: "synthetic_public",
        },
      ],
    });
    qualificationRoutes.mockResolvedValue({
      items: [
        {
          capability: "chat",
          configured: true,
          route_enabled: false,
          network_enabled: false,
          qualified: false,
          model_identifier: "Qwen/Qwen3.5-35B-A3B",
          sanitized_base_host: "api.siliconflow.cn",
          provider_protocol: "openai_compatible",
          suite_version: "2026.09.05",
          price_catalog_revision: "siliconflow-2026-09-05",
          fingerprint: "abc",
          activates_production: false,
        },
      ],
    });
    qualificationDecisions.mockResolvedValue({
      items: [
        {
          capability: "chat",
          status: "unqualified",
          qualifies: false,
          activates_production: false,
          configured: true,
          route_enabled: false,
          model_identifier: "Qwen/Qwen3.5-35B-A3B",
          sanitized_base_host: "api.siliconflow.cn",
          suite_version: "none",
          blocker_codes: ["default_denied"],
          advisory_score: null,
          run_id: null,
          evaluated_at: "2026-09-05T08:00:00+00:00",
          stale_reason: null,
        },
      ],
    });
    qualificationRuns.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
  });

  it("shows configured and unqualified as distinct states", async () => {
    render(<QualificationPage />);
    expect(await screen.findByText("已配置但未鉴定，不能当作就绪或生产启用。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "模型资格" })).toBeInTheDocument();
    expect(screen.queryByText("生产已启用")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览受控实跑" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "鉴定历史" }).closest("section")).not.toHaveClass(
      "h-full",
    );
  });
});
