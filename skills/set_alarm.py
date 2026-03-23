import datetime
import time
def set_alarm(alarm_time_str: str, message: str) -> str:
    """
    设置闹钟，在指定的时间发出提醒。
    
    :param alarm_time_str: 闹钟触发的时间，格式为 'HH:MM'。
    :param message: 提醒信息。
    :return: 确认信息或错误信息。
    """
    try:
        # 解析输入的时间字符串
        alarm_hour, alarm_minute = map(int, alarm_time_str.split(':'))
        now = datetime.datetime.now()
        
        # 计算从现在到闹钟触发所需等待的时间
        alarm_time = now.replace(hour=alarm_hour, minute=alarm_minute, second=0, microsecond=0)
        if alarm_time < now:
            alarm_time += datetime.timedelta(days=1)  # 如果设置的时间已经过去，则默认为明天
        
        wait_seconds = (alarm_time - now).total_seconds()
        
        # 等待直到闹钟触发
        time.sleep(wait_seconds)
        
        # 触发闹钟
        return f"Alarm triggered! Message: {message}"
    except ValueError:
        return "Invalid time format. Please use HH:MM."