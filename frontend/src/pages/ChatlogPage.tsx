import { useEffect, useRef, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type ChatlogItem, type ChatlogPeer } from "../lib/api";
import { peerSubtitle, peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

export function ChatlogPage() {
  const labels = useOperatorLabels();
  const [peers, setPeers] = useState<ChatlogPeer[]>([]);
  const [kind, setKind] = useState<"private" | "group">("private");
  const [peerId, setPeerId] = useState("");
  const [items, setItems] = useState<ChatlogItem[]>([]);
  const [error, setError] = useState("");
  const itemsRef = useRef<ChatlogItem[]>([]);

  async function loadPeers() {
    const body = await api.chatlogPeers();
    setPeers(body.items);
    if (!peerId && body.items[0]) {
      setKind(body.items[0].kind === "group" ? "group" : "private");
      setPeerId(body.items[0].peer_id);
    }
  }

  async function loadMessages(nextKind = kind, nextPeer = peerId, afterId = "") {
    if (!nextPeer) {
      setItems([]);
      itemsRef.current = [];
      return;
    }
    const body = await api.chatlog(nextKind, nextPeer, afterId);
    setItems((current) => {
      const next = afterId ? [...current, ...body.items] : body.items;
      itemsRef.current = next;
      return next;
    });
  }

  function changeKind(next: "private" | "group") {
    setKind(next);
    const match = peers.find((item) => item.kind === next);
    setPeerId(match?.peer_id ?? "");
    setItems([]);
    itemsRef.current = [];
  }

  useEffect(() => {
    loadPeers().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    loadMessages().catch((err: Error) => setError(err.message));
  }, [kind, peerId]);

  useEffect(() => {
    if (!peerId) return;
    const timer = window.setInterval(() => {
      const last = itemsRef.current[itemsRef.current.length - 1]?.id ?? "";
      loadMessages(kind, peerId, last).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [kind, peerId]);

  return (
    <PageFrame>
      <PageTitle
        title="聊天记录"
        description="只看当天。过了自然日会自动空出来。用户和机器人用不同颜色的气泡。"
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-[minmax(240px,0.8fr)_minmax(0,1.6fr)]">
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">今天的对象</h2>
          <div className="mt-4">
            <Select
              value={kind}
              onChange={(next) => changeKind(next as "private" | "group")}
              options={[
                { value: "private", label: "私聊" },
                { value: "group", label: "群聊" },
              ]}
            />
          </div>
          <ul className="mt-3 divide-y divide-black/5">
            {peers.filter((item) => item.kind === kind).length === 0 ? (
              <li>
                <EmptyState tone="inline" title="今天还没有这类对话。" />
              </li>
            ) : (
              peers
                .filter((item) => item.kind === kind)
                .map((item) => (
                  <li key={`${item.kind}-${item.peer_id}`}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between rounded-2xl px-2 py-3 text-left ${
                        peerId === item.peer_id ? "bg-muted/70" : "hover:bg-muted/50"
                      }`}
                      onClick={() => setPeerId(item.peer_id)}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-foreground">
                          {peerTitle(
                            item.peer_id,
                            item.kind === "group"
                              ? labels.group(item.peer_id)
                              : labels.user(item.peer_id),
                          )}
                        </span>
                        {peerSubtitle(
                          item.peer_id,
                          item.kind === "group"
                            ? labels.group(item.peer_id)
                            : labels.user(item.peer_id),
                        ) ? (
                          <span className="block truncate text-xs text-muted-foreground">
                            {item.peer_id}
                          </span>
                        ) : null}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {item.messages} 条
                      </span>
                    </button>
                  </li>
                ))
            )}
          </ul>
        </SurfaceCard>
        <SurfaceCard fill className="min-h-[420px]">
          <h2 className="text-lg font-semibold text-foreground">{peerId || "选择一个对象"}</h2>
          <div className="mt-4 flex max-h-[70vh] flex-col gap-3 overflow-y-auto">
            {items.length === 0 ? (
              <EmptyState
                tone="select"
                title="当天还没有消息，或还没有镜像出站记录。"
              />
            ) : (
              items.map((item) => {
                const bot = item.direction === "outbound";
                return (
                  <div key={item.id} className={`flex ${bot ? "justify-end" : "justify-start"}`}>
                    <div
                      className={`max-w-[80%] rounded-3xl px-4 py-3 text-sm ${
                        bot ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
                      }`}
                    >
                      <p className="whitespace-pre-wrap">{item.plain_text || "（非文本）"}</p>
                      <p className={`mt-1 text-[11px] ${bot ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                        {item.occurred_at.slice(11, 16)}
                      </p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
