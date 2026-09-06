import { useCallback, useEffect, useMemo, useState } from "react";
import type { OperatorLabel } from "./api";
import { api } from "./api";
import { lookupLabel, operatorLabelMap } from "./peerLabels";

export function useOperatorLabels() {
  const [items, setItems] = useState<OperatorLabel[]>([]);

  const reload = useCallback(async () => {
    const body = await api.operatorLabels();
    setItems(body.items);
  }, []);

  useEffect(() => {
    reload().catch(() => setItems([]));
  }, [reload]);

  const labels = useMemo(() => operatorLabelMap(items), [items]);

  return useMemo(
    () => ({
      items,
      reload,
      user: (id: string) => lookupLabel(labels, "user", id),
      group: (id: string) => lookupLabel(labels, "group", id),
    }),
    [items, labels, reload],
  );
}
