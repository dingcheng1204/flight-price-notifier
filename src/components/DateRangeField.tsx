import { useState } from "react";
import { CalendarDays, X } from "lucide-react";
import type { DateRange, Matcher } from "react-day-picker";
import { zhTW } from "date-fns/locale/zh-TW";

import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useIsMobile } from "@/hooks/use-mobile";
import { isoDate, parseIsoDate, rangeLabel } from "@/lib/flight-api";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  from?: string | undefined;
  to?: string | undefined;
  /** Days before this (YYYY-MM-DD) can't be picked. */
  minDate: string;
  onChange: (from: string | undefined, to: string | undefined) => void;
  placeholder?: string;
  /** Month the calendar opens on when nothing is selected. */
  defaultMonth?: string | undefined;
  invalid?: boolean;
}

/** A button that opens a two-month range calendar (one month on phones). */
export function DateRangeField({
  label,
  from,
  to,
  minDate,
  onChange,
  placeholder = "選擇日期 Pick dates",
  defaultMonth,
  invalid,
}: Props) {
  const [open, setOpen] = useState(false);
  const isMobile = useIsMobile();
  const selected: DateRange | undefined = from
    ? { from: parseIsoDate(from), to: to ? parseIsoDate(to) : undefined }
    : undefined;
  const disabled: Matcher = { before: parseIsoDate(minDate) };

  return (
    <div className="text-sm">
      <span className="mb-1 block text-muted-foreground">{label}</span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={label}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg border bg-muted px-3 py-2 text-left",
              !from && "text-muted-foreground",
              invalid && "border-destructive",
            )}
          >
            <CalendarDays className="h-4 w-4 shrink-0 opacity-70" />
            <span className="truncate">{from ? rangeLabel(from, to) : placeholder}</span>
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="range"
            locale={zhTW}
            numberOfMonths={isMobile ? 1 : 2}
            selected={selected}
            defaultMonth={selected?.from ?? parseIsoDate(defaultMonth ?? minDate)}
            disabled={disabled}
            startMonth={parseIsoDate(minDate)}
            onSelect={(r) =>
              onChange(r?.from ? isoDate(r.from) : undefined, r?.to ? isoDate(r.to) : undefined)
            }
            className="rounded-lg"
          />
          <div className="flex items-center justify-between gap-2 border-t px-3 py-2 text-xs">
            <span className="text-muted-foreground">
              {from ? rangeLabel(from, to) : "點選開始日，再點選結束日"}
            </span>
            <div className="flex gap-2">
              {from && (
                <button
                  type="button"
                  onClick={() => onChange(undefined, undefined)}
                  className="flex items-center gap-1 rounded-md px-2 py-1 hover:bg-accent"
                >
                  <X className="h-3 w-3" /> 清除
                </button>
              )}
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md bg-primary px-3 py-1 text-primary-foreground"
              >
                完成
              </button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
