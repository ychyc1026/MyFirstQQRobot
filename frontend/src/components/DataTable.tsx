import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { Button } from "./ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

export type DataColumn<T> = { key: string; label: string; className?: string; render: (row: T) => ReactNode };

export function DataTable<T>({ rows, columns, rowKey, page = 1, pageCount = 1, onPageChange, empty }: { rows: T[]; columns: DataColumn<T>[]; rowKey: (row: T) => string; page?: number; pageCount?: number; onPageChange?: (page: number) => void; empty?: ReactNode }) {
  if (!rows.length) return <>{empty ?? <EmptyState title="暂无记录" />}</>;
  return <div className="space-y-3">
    <div className="overflow-x-auto rounded-md border">
      <Table className="min-w-[760px]">
        <TableHeader><TableRow>{columns.map((column) => <TableHead key={column.key} className={column.className}>{column.label}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{rows.map((row) => <TableRow key={rowKey(row)}>{columns.map((column) => <TableCell key={column.key} className={column.className}>{column.render(row)}</TableCell>)}</TableRow>)}</TableBody>
      </Table>
    </div>
    {pageCount > 1 ? <div className="flex items-center justify-end gap-2"><span className="mr-auto text-xs text-muted-foreground">第 {page} / {pageCount} 页</span><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPageChange?.(page - 1)}>上一页</Button><Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => onPageChange?.(page + 1)}>下一页</Button></div> : null}
  </div>;
}
