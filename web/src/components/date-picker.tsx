import { useState } from "react";
import { format, parse, isValid } from "date-fns";
import { CalendarIcon } from "lucide-react";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

function parseValue(value: string): Date | undefined {
  if (!value) return undefined;
  // Try YYYY-MM-DD first
  const full = parse(value, "yyyy-MM-dd", new Date());
  if (isValid(full)) return full;
  // Try MM-DD (use a dummy year for the picker)
  const partial = parse(value, "MM-dd", new Date(2000, 0, 1));
  if (isValid(partial)) return partial;
  return undefined;
}

export function DatePicker({
  value,
  onChange,
  placeholder = "Pick a date",
  includeYear = true,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  includeYear?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [yearOptional, setYearOptional] = useState(!includeYear || !!(value && !value.includes("-", 3)));

  const date = parseValue(value);

  function handleSelect(selected: Date | undefined) {
    if (!selected) return;
    if (yearOptional) {
      onChange(format(selected, "MM-dd"));
    } else {
      onChange(format(selected, "yyyy-MM-dd"));
    }
    setOpen(false);
  }

  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className={cn(
                "flex-1 justify-start text-left font-normal",
                !value && "text-muted-foreground",
              )}
            >
              <CalendarIcon className="mr-2 h-4 w-4" />
              {value || placeholder}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="single"
              selected={date}
              onSelect={handleSelect}
              defaultMonth={date}
              captionLayout="dropdown"
              startMonth={new Date(1900, 0)}
              endMonth={new Date()}
            />
          </PopoverContent>
        </Popover>
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={yearOptional ? "MM-DD" : "YYYY-MM-DD"}
          className="w-32"
        />
      </div>
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={yearOptional}
          onChange={(e) => setYearOptional(e.target.checked)}
          className="rounded"
        />
        No birth year
      </label>
      <p className="text-xs text-muted-foreground">
        Format: {yearOptional ? "MM-DD" : "YYYY-MM-DD"}
        {value && !date && (
          <span className="text-destructive"> - that isn't a valid date</span>
        )}
      </p>
    </div>
  );
}
