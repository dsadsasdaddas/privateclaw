interface ScheduledAlarm {
  status: "scheduled" | "triggered";
  message: string;
  triggerAt: Date;
}

const scheduledAlarms = new Map<string, ScheduledAlarm>();

export function setAlarm(alarmTimeStr: string, message: string): string {
  const [hourRaw, minuteRaw] = alarmTimeStr.split(":");
  const hour = Number(hourRaw);
  const minute = Number(minuteRaw);
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return "Invalid time format. Please use HH:MM.";
  }

  const now = new Date();
  const triggerAt = new Date(now);
  triggerAt.setHours(hour, minute, 0, 0);
  if (triggerAt.getTime() < now.getTime()) {
    triggerAt.setDate(triggerAt.getDate() + 1);
  }

  const alarmId = Math.random().toString(16).slice(2, 10);
  const waitMs = triggerAt.getTime() - now.getTime();
  scheduledAlarms.set(alarmId, { status: "scheduled", message, triggerAt });

  setTimeout(() => {
    const item = scheduledAlarms.get(alarmId);
    if (item) {
      item.status = "triggered";
      console.log(`[ALARM][${alarmId}] ${message}`);
    }
  }, waitMs).unref?.();

  return `Alarm scheduled. alarm_id=${alarmId}, trigger_at=${triggerAt.toISOString()}, message=${message}`;
}
