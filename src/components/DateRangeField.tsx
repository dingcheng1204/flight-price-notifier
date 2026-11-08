import { useState } from "react";
import { CalendarDays, X } from "lucide-react";
import type { DateRange, Matcher } from "react-day-picker";
import { zhTW } from "date-fns/locale/zh-TW";

import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { MAX_RANGE_DAYS, isoDate, nextRange, parseIsoDate, rangeLabel } from "@/lib/flight-api";
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

/** Last day an end date may fall on: a range covers at most MAX_RANGE_DAYS days, inclusive. */
function lastAllowed(from: string): Date {
  const d = parseIsoDate(from);
  d.setDate(d.getDate() + MAX_RANGE_DAYS - 1);
  return d;
}

/** A button that opens a one-month range calendar. */
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
  const selected: DateRange | undefined = from
    ? { from: parseIsoDate(from), to: to ? parseIsoDate(to) : undefined }
    : undefined;
  // While an end date is pending, days past the 60-day limit are greyed out.
  const disabled: Matcher[] = [{ before: parseIsoDate(minDate) }];
  if (from && !to) disabled.push({ after: lastAllowed(from) });

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
        {/* bg-background = the theme's blue-green sky gradient. The calendar sets the width; the
            footer (w-0 min-w-full) never widens it, so the popover keeps one size while picking. */}
        <PopoverContent className="w-auto bg-background p-0" align="start">
          <Calendar
            mode="range"
            locale={zhTW}
            numberOfMonths={1}
            selected={selected}
            defaultMonth={selected?.from ?? parseIsoDate(defaultMonth ?? minDate)}
            disabled={disabled}
            startMonth={parseIsoDate(minDate)}
            onSelect={(_range, day) => {
              const [f, t] = nextRange(from, to, isoDate(day));
              onChange(f, t);
            }}
            className="mx-auto bg-transparent [--cell-size:2.25rem]"
          />
          <div className="flex w-0 min-w-full items-center justify-between gap-2 border-t px-3 py-2 text-xs">
            {/* fixed two-line height so the hint can change without resizing the popover */}
            <span className="flex min-h-8 min-w-0 flex-1 items-center text-muted-foreground">
              {from ? (
                to ? (
                  rangeLabel(from, to)
                ) : (
                  <span className="whitespace-nowrap">
                    {rangeLabel(from)} 起<br />
                    選結束日，最多 {MAX_RANGE_DAYS} 天
                  </span>
                )
              ) : (
                "點選開始日與結束日"
              )}
            </span>
            <div className="flex shrink-0 gap-2">
              {/* always laid out (just hidden) so the footer doesn't reflow */}
              <button
                type="button"
                onClick={() => onChange(undefined, undefined)}
                className={cn(
                  "flex items-center gap-1 rounded-md px-2 py-1 hover:bg-accent",
                  !from && "invisible",
                )}
                tabIndex={from ? 0 : -1}
                aria-hidden={!from}
              >
                <X className="h-3 w-3" /> 清除
              </button>
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
