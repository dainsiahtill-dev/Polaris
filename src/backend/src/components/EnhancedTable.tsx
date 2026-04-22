// Enhanced Data Table Component - Materialization Phase
import React, { useState, useMemo, useCallback } from 'react';

interface Column<T> {
  key: keyof T | string;
  title: string;
  sortable?: boolean;
  filterable?: boolean;
  width?: string | number;
  render?: (value: any, record: T, index: number) => React.ReactNode;
}

interface EnhancedTableProps<T> {
  data: T[];
  columns: Column<T>[];
  onSort?: (key: string, direction: 'asc' | 'desc') => void;
  onFilter?: (filters: Record<string, any>) => void;
  virtualScroll?: boolean;
  rowKey: keyof T;
}

export function EnhancedTable<T extends Record<string, any>>({
  data,
  columns,
  onSort,
  onFilter,
  virtualScroll = false,
  rowKey,
}: EnhancedTableProps<T>) {
  const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(null);
  const [filters, setFilters] = useState<Record<string, any>>({});
  const [scrollPosition, setScrollPosition] = useState(0);

  const handleSort = useCallback((key: string) => {
    setSortConfig(prev => {
      const newDirection = prev?.key === key && prev.direction === 'asc' ? 'desc' : 'asc';
      onSort?.(key, newDirection);
      return { key, direction: newDirection };
    });
  }, [onSort]);

  const sortedData = useMemo(() => {
    if (!sortConfig) return data;
    return [...data].sort((a, b) => {
      const aVal = a[sortConfig.key];
      const bVal = b[sortConfig.key];
      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return sortConfig.direction === 'asc' ? comparison : -comparison;
    });
  }, [data, sortConfig]);

  const filteredData = useMemo(() => {
    return sortedData.filter(row => {
      return Object.entries(filters).every(([key, value]) => {
        if (!value) return true;
        return String(row[key]).toLowerCase().includes(String(value).toLowerCase());
      });
    });
  }, [sortedData, filters]);

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollPosition(e.currentTarget.scrollTop);
  }, []);

  return (
    <div className="enhanced-table-container" onScroll={handleScroll}>
      {onFilter && (
        <div className="table-filters">
          {columns.filter(col => col.filterable).map(col => (
            <input
              key={String(col.key)}
              placeholder={`Filter ${col.title}`}
              onChange={e => {
                const newFilters = { ...filters, [col.key]: e.target.value };
                setFilters(newFilters);
                onFilter(newFilters);
              }}
              value={filters[col.key] || ''}
            />
          ))}
        </div>
      )}
      <table className="enhanced-table">
        <thead>
          <tr>
            {columns.map(col => (
              <th
                key={String(col.key)}
                style={{ width: col.width }}
                onClick={() => col.sortable && handleSort(String(col.key))}
              >
                {col.title}
                {col.sortable && sortConfig?.key === col.key && (
                  <span>{sortConfig.direction === 'asc' ? ' ↑' : ' ↓'}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filteredData.map((row, index) => (
            <tr key={String(row[rowKey])}>
              {columns.map(col => (
                <td key={String(col.key)}>
                  {col.render
                    ? col.render(row[col.key as keyof T], row, index)
                    : String(row[col.key as keyof T] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
