import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithRouter } from "../test/support/render";
import { SystemPage } from "./SystemPage";

vi.mock("../lib/api", () => ({
  api: {
    identity: vi.fn(),
    status: vi.fn(),
    privacyStatus: vi.fn(),
    systemPreflight: vi.fn(),
    controlCommands: vi.fn(),
    privacyRequests: vi.fn(),
    readinessSummary: vi.fn(),
    readinessCurrent: vi.fn(),
    refreshReadiness: vi.fn(),
    knowledgeWorkerPause: vi.fn(),
    qzoneWorkerPause: vi.fn(),
    proactiveWorkerPause: vi.fn(),
    outboxWorkerPause: vi.fn(),
    reportWorkerPause: vi.fn(),
    runCommand: vi.fn(),
  },
}));

import { api } from "../lib/api";

describe("SystemPage observe status", () => {
  beforeEach(() => {
    vi.mocked(api.identity).mockResolvedValue({
      brand: "YCH",
      creator_name: "YCH",
      locked: true,
    });
    vi.mocked(api.status).mockResolvedValue({
      mode: "observe_only",
      onebot: {
        connected: true,
        last_event_at: "2026-09-05T12:47:23+00:00",
        auth_configured: true,
        authenticated_bot_qq: "2000000002",
        exact_bot: true,
      },
      control: {
        owner_reports_enabled: false,
        qzone_publish_enabled: false,
        qzone_profile_collection_enabled: false,
      },
      models: { chat: { configured: false, network_enabled: false, route_enabled: false } },
    });
    vi.mocked(api.privacyStatus).mockResolvedValue({
      default_history_access: "none",
      live_history_read_enabled: false,
      qzone_profile_collection_enabled: false,
      privacy_jobs_enabled: false,
      document_import_enabled: false,
      document_ocr_enabled: false,
      image_content_review_enabled: false,
      image_orphan_scan_enabled: false,
      shadow_inference_enabled: false,
      knowledge_processing_enabled: false,
      frozen_users: 0,
    });
    vi.mocked(api.systemPreflight).mockResolvedValue({
      current_schema_version: 34,
      latest_schema_version: 34,
      integrity_ok: true,
      backup_enabled: true,
      backup_health: {
        verified: true,
        verified_count: 0,
        backup_count: 0,
        total_bytes: 0,
        retention: {
          keep_latest: 3,
          minimum_age_days: 90,
          automatic_cleanup: false,
          candidate_count: 0,
          candidate_bytes: 0,
        },
      },
    } as Awaited<ReturnType<typeof api.systemPreflight>>);
    vi.mocked(api.controlCommands).mockResolvedValue({ items: [] });
    vi.mocked(api.privacyRequests).mockResolvedValue({ items: [] });
    vi.mocked(api.readinessSummary).mockResolvedValue({
      bot_qq: "2000000002",
      process_instance_id: "process-a",
      controlled_scope: "local_runtime",
      profiles: [],
    });
  });

  it("shows connected exact-bot identity without implying delivery", async () => {
    renderWithRouter(<SystemPage />);
    expect(await screen.findByText("已连接，机器人身份匹配")).toBeInTheDocument();
    expect(screen.getByText("当前为观察模式，不会外发。")).toBeInTheDocument();
    expect(screen.getByText("观察模式")).toBeInTheDocument();
    expect(screen.queryByText("主动模式")).not.toBeInTheDocument();
  });
});
