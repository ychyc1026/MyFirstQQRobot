import type { OperatorLabel } from "./api";

export function peerTitle(id: string, label?: string | null) {
  const name = label?.trim();
  return name || id;
}

export function peerSubtitle(id: string, label?: string | null) {
  return label?.trim() ? id : "";
}

export function labelKey(kind: "user" | "group", id: string) {
  return `${kind}:${id}`;
}

export function operatorLabelMap(items: OperatorLabel[]) {
  return new Map(items.map((item) => [labelKey(item.subject_kind, item.subject_id), item.label]));
}

export function lookupLabel(
  labels: Map<string, string>,
  kind: "user" | "group",
  id: string,
) {
  return labels.get(labelKey(kind, id)) ?? "";
}
