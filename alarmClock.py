import sqlite3, re, datetime, json, subprocess, threading, time, signal, os
import tkinter.messagebox as msgBox
from prettytable import PrettyTable

from dotenv import dotenv_values

# читаем .env файлик
env = dotenv_values()
# открываем соединение с базой
dbCon = sqlite3.connect(env.get('dbFile', 'ac.db') )

cursor = dbCon.cursor()
# добавляем таблицу будильников, если её нет
cursor.execute('''create table if not exists alarms (
    id integer,
    time integer  not null,
    cond text,
    pid integet,
    constraint pk primary key (id autoincrement)
);''')
cursor.close()

soundDir = env.get('soundDir', None)

# Список, того что можно поиграть ....
soundFiles = []

if soundDir and os.path.exists(soundDir):
    soundFiles = [sItem for sItem in os.scandir(soundDir) if sItem.is_file()]


class Alarm:
    ''' Класс одного будильника '''
    # дни недели
    DAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    # колонки загружаемые из таблицы будильников как отдельные поля
    ALARM_COLUMNS = ('id', 'time', 'cond', 'pid', )
    # Наличие ошибок валидации
    __hasErrors = False


    @classmethod
    def getAll(cls):
        ''' достать все записи будильников '''
        cursor = dbCon.cursor()
        cursor.execute('select * from alarms')
        rows = cursor.fetchall()
        cursor.close()
        return [cls(dict(zip(cls.ALARM_COLUMNS, row))) for row in rows]


    @classmethod
    def getById(cls, aId):
        ''' загрузить будильник по id
            :param aId: Номер будильника в базе
        '''
        cursor = dbCon.cursor()
        cursor.execute('select * from alarms where id = ?', (aId,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            raise ValueError('Будильник не найден')
        return cls(dict(zip(cls.ALARM_COLUMNS, row)))

    @classmethod
    def ringerAlarms(cls, usedDbCon=None):
        ''' Забрать будильники совпавшие с текущим временем
            :param usedDbCon: используемое соединение для доступа к базе, Если пусто - используем соединение основного потока
        '''
        curTime = time.localtime()
        ct = curTime.tm_hour * 60 + curTime.tm_min
        dbCon2 = usedDbCon if usedDbCon else dbCon
        cursor = dbCon2.cursor()
        cursor.execute('select * from alarms where time = ?', (ct,))
        alarms = cursor.fetchall()
        cursor.close()
        # dbCon2.close()
        return tuple(filter(lambda a: a.available(), map(lambda el: cls( dict(zip(('id', 'time', 'cond'), el))), alarms)))


    @classmethod
    def stopAll(self, usedDbCon=None):
        ''' Остановка всех запущенных будильников ..
            :param usedDbCon: Используемое соединнение с базой
        '''
        dbCon2 = usedDbCon if usedDbCon else dbCon
        curs = dbCon2.cursor()
        curs.execute('select id as aid, pid from alarms where pid > 0')
        pids = curs.fetchall()
        if not pids:
            return
        for (aid, pid,) in pids:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            curs.execute('update alarms set pid = null where id = ?', (aid,))
        dbCon2.commit()


    def available(self):
        ''' проверка условий '''
        curTime = time.localtime()
        # проверка по времени запуска... (первый запуск будильника)
        if self._time != curTime.tm_hour * 60 + curTime.tm_min:
            return False
        # проверка даты если есть ...
        if 'date' in self._cond and self._cond['date'] != f'{curTime.tm_mday:02d}.{curTime.tm_mon:02d}.{curTime.tm_year}':
            return False
        # вылет по дням недели...
        if 'days' in self._cond and self.DAYS[curTime.tm_wday] not in self._cond['days']:
            return False
        return True


    def __init__(self, *args):
        ''' Инициализация объекта будильника '''
        self._id = None # номер будильника
        self._pid = None # номер приложения-звонилки ....
        # проверка на наличие в первом аргументе словаря - словарь - данные из базы
        if len(args) == 1 and isinstance(args[0], dict):
            for k in args[0].keys():
                setattr(self, '_' + k, json.loads(args[0][k]) if k == 'cond' else args[0][k])
            return

        # создание нового объекта из исходных данных
        if len(args) == 5:
            try:
                self.__initFrom3Args(*args)
            except BaseException as e:
                self.__hasErrors = True
                raise e


    def __initFrom3Args(self, time='', when=None, repeat=None, msg=None, soundNum=None):
        ''' заполнение полей будильника из пользовательского ввода  '''
        time = re.match(r'^\d{2}:\d{2}$', time.strip())
        # Криво задано  время звонка ..
        if time is None:
            raise TypeError('Не верный формат времени')
        time = list(map(lambda v: int(v), time.group(0).split(':')))
        # проверка предельных интервалов
        if time[0] > 23 or time[1] > 59:
            raise TypeError('Выход за пределы интервалов указания времени')
        # Сохраняем время в минутах
        self._time = time[1] + time[0] * 60
        self._cond = {}
        # думаем, что задана дата
        if when and when != '-':
            v = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', when.strip())
            if v is None:
                # с датой не получилось. пробуем разбить на части по запятой (вдруг это дни недели)
                v = [v for v in re.split(r'\s*,\s*', when) if self.DAYS.count(v) > 0]
            else:
                now = datetime.datetime.now()
                alarm = datetime.datetime(int(v.group(3)), int(v.group(2)), int(v.group(1)), self._time // 60, self._time % 60)
                sub = (alarm - now).total_seconds()
                if sub <= 0:
                    raise ValueError('Будильник не зазвонит: дата в прошлом')
            if isinstance(v, re.Match):
                self._cond['date'] = v.group(0)
            else:
                self._cond['days'] = v
        # повторы
        if repeat and repeat != '-':
            r = re.match(r'^(\d+):(\d+)$', repeat)
            if r is None:
                raise TypeError('Неверно задан повтор будильника')
            repeat = {'count': int(r.group(1)), 'interval': int(r.group(2)) }
            if repeat['count'] * repeat['interval'] >= 24 * 60:
                raise ValueError('Слишком длинные повторы')
            self._cond.update(repeat)

        # сообщение
        if msg:
            self._cond['msg'] = msg

        # мелодия
        if soundNum and soundNum.isdigit():
            soundNum = int(soundNum) - 1
            if soundNum >= 0 and soundNum < len(soundFiles):
                self._cond['soundN'] = soundNum


    def save(self):
        ''' сохранение будильника'''
        # Есть ошибки .. сохраняться не будем .
        if self.__hasErrors:
            return False
        dataRow = (self._time, json.dumps(self._cond, ensure_ascii=False),)
        cursor = dbCon.cursor()
        cursor.execute('insert into alarms (time, cond) values(?, ?)', dataRow)
        dbCon.commit()
        self._id = cursor.lastrowid
        cursor.close()
        return True


    def delete(self):
        ''' Удаление записи будильника из базы '''
        # Нельзя удалить то чего ещё нет
        if not self._id:
            return False
        self.stopDing()
        cursor = dbCon.cursor()
        cursor.execute('delete from alarms where id = ?', (self._id,))
        dbCon.commit()
        cursor.close()
        return True


    def __showMsg(self):
        ''' Показываем сообщение '''
        msgBox.showinfo(self, self._cond['msg'] if 'msg' in self._cond and self._cond['msg'] else  'Звоним!')

    def startDing(self, dbThreadCon=None):
        ''' запуск звонилки .. .для конкретного будильника
            :param dbThreadCon: Используемое соединение с базой, если пусто - используем основной поток
        '''
        dbCon2 = dbThreadCon if dbThreadCon else dbCon
        if self._pid: # будильник уже звонил на момент повтора  - надо остановить и сделать перезапуск
            self.stopDing(dbCon2)

        # print('ding', self._id, '!!')

        playerApp = env.get('player', None)
        soundTrack = env.get('sound', None)
        if 'soundN' in self._cond and self._cond['soundN'] >= 0 and self._cond['soundN'] < len(soundFiles):
            soundTrack = soundFiles[self._cond['soundN']].path

        if playerApp and soundTrack:
            proc = subprocess.Popen([playerApp, soundTrack, '--volume=30'], stdout=subprocess.DEVNULL)
            self._pid = proc.pid
            curs = dbCon2.cursor()
            curs.execute('update alarms set pid = ? where id = ?', (self._pid, self._id,))
            dbCon2.commit()
            curs.close()
            # показываем сообщение ...
            threading.Thread(target=self.__showMsg).start()

    def stopDing(self, dbThreadCon=None):
        ''' остановка запущенного будильника
            :param dbThreadCon: соединение с базой используемое в потоке
        '''
        if self._pid is None:
            return False

        try:
            os.kill(self._pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        dbCon2 = dbThreadCon if dbThreadCon else dbCon
        curs = dbCon2.cursor()
        curs.execute('update alarms set pid = null where id = ?', (self._id,))
        dbCon2.commit()
        curs.close()
        return True


    def __repr__(self):
        res = [f'alarm #{self._id} [в {self.time}']
        if 'date' in self._cond:
            res.append(f'{self._cond["date"]}')
        if 'days' in self._cond:
            res.append(f'каждые {",".join(self._cond["days"])}')
        if len({'date', 'days'} & set(self._cond.keys())) == 0:
            res.append('каждый день')
        if 'count' in self._cond and self._cond['count'] > 0:
            res.append(f'{self._cond["count"]} п.')
        if 'interval' in self._cond:
            res.append(f'через {self._cond["interval"]} мин')
        return ' '.join(res) + ']'


    @property
    def id(self):
        ''' возвращаем id будильника '''
        return self._id


    @property
    def time(self):
        ''' возвращаем время звонка в виде строки'''
        return  f'{self._time // 60:02d}:{self._time % 60:02d}'


    @property
    def timeAsDiget(self):
        ''' возвращаем время звонка: минут в день'''
        return self._time


    @property
    def when(self):
        ''' возвращаем ограничения на звонок '''
        return self._cond['date'] if 'date' in self._cond else  ', '.join(self._cond['days']) if 'days' in self._cond else 'каждый день'


    @property
    def repeatsTuple(self):
        ''' число повторов в кортежа  '''
        if 'count' in self._cond and 'interval' in self._cond:
            return (self._cond['count'], self._cond['interval'],)
        return None

    @property
    def isRing(self):
        ''' будильник в активном режиме - звонит '''
        return self._pid is not None

    @property
    def repeats(self):
        ''' число повторов в виде строки '''
        if 'count' in self._cond and 'interval' in self._cond:
            return f'{self._cond['count']} через {self._cond['interval']} мин.'
        return '-'



class AlarmClock:
    ''' Класс управления будильниками '''

    # проверка будильников работает до тех пор пока тут True
    __ringerAwailable = True
    # Набор будильников с повторами ... (их нужно повторить )
    __alarmsWithRepeat = {}
    # содинение базы для потока проверяющего и запускающего будильники ..
    __dbConnectionAlarmsCheckerThread = None


    def __alarmRingerRepeatTodo(self):
        ''' проверка и запуск повторов будильников  '''
        curTime = time.localtime()
        curTime = curTime.tm_hour*60 + curTime.tm_min
        toDel = []
        # пробегаем по повторам ..
        for aId in filter(lambda ark: curTime in self.__alarmsWithRepeat[ark]['times'], self.__alarmsWithRepeat):
            self.__alarmsWithRepeat[aId]['alarm'].startDing(self.__dbConnectionAlarmsCheckerThread)
            self.__alarmsWithRepeat[aId]['times'].pop(0)
            # Повторы закончились ... пемечаем для удаления
            if len(self.__alarmsWithRepeat[aId]['times']) == 0:
                toDel.append(aId)
        for aId in toDel:
            del self.__alarmsWithRepeat[aId]


    def __alarmRingerFirstRinger(self, alarm):
        ''' заполнение повторов - первый запуск будильника
            :param alarm: объект запущенного будильника
        '''
        # Включаем звонилку ...
        alarm.startDing(self.__dbConnectionAlarmsCheckerThread)
        # alarmDinger = threading.Thread(target=self.__soundRinger)
        #     alarmDinger.start()

        # Запрос повторов у будильника
        reps = alarm.repeatsTuple
        # нет повторов
        if not reps:
            return

        # на всякий пожарный запихаем сам будильник
        self.__alarmsWithRepeat[alarm.id] = {'alarm': alarm, 'times': [alarm.timeAsDiget]}
        # собираем времена повторов
        for t in range(reps[0]):
            nt = self.__alarmsWithRepeat[alarm.id]['times'][-1] + reps[1]
            # перенос через сутки
            nt = nt if nt < 24 * 60 else nt % (24 * 60)
            self.__alarmsWithRepeat[alarm.id]['times'].append(nt)
        # выкинуть то что уже случилось
        self.__alarmsWithRepeat[alarm.id]['times'].pop(0)


    def __alarmRinger(self, sStart = None):
        ''' проверяем состояния будильников ..
            :param sStart: Секунда реального времени на которой был запуск
        '''
        self.__dbConnectionAlarmsCheckerThread = sqlite3.connect(env.get('dbFile', 'ac.db'))
        # просто счётчик
        s = sStart if sStart else 0
        # будильники с повтором
        self.__alarmsWithRepeat = {}
        # сразу проверяем будильники ... вдруг кто всплыл
        alarms = Alarm.ringerAlarms(self.__dbConnectionAlarmsCheckerThread)
        # по всем найденным - собираем повторы (если есть)
        for alarm in alarms:
            self.__alarmRingerFirstRinger(alarm)

        ''' звонилка для будильника . '''
        while self.__ringerAwailable:
            time.sleep(1)
            # на  первой секунде каждой минуты ...
            if s == 1:
                self.__alarmRingerRepeatTodo()

                # ищем будильники совпавшие по всем параметрам (певый звонок)
                alarms = Alarm.ringerAlarms(self.__dbConnectionAlarmsCheckerThread)
                # по всем найденным - собираем повторы (если есть)
                for alarm in alarms:
                    self.__alarmRingerFirstRinger(alarm)
            s += 1
            if s > 59:
                s = 0

        # Остановка запущенных будильников ...
        Alarm.stopAll(self.__dbConnectionAlarmsCheckerThread)
        # signal.SIGTERM
        self.__dbConnectionAlarmsCheckerThread.close()


    def __init__(self):
        ''' главный цикл приложения '''
        print('Для справки введите "help"\nвыход - пустая команда')
        t = time.localtime();
        # запуск потока опроса базы на наличие подходящих будильников
        self.__ringer = threading.Thread(target = self.__alarmRinger, args = (t.tm_sec,))
        self.__ringer.start()
        # цикл опроса прользователя
        while True:
            cmd = input('Введите команду: ').strip()
            if not cmd:
                print('Выходим из приложенния...')
                break
            args = re.split(r'\s+', cmd)
            action = '_todo' + ''.join(map(lambda s: s.title(), args.pop(0).split('-')))
            if not dir(self).count(action):
                continue
            action = getattr(self, action)
            try:
                action(*args)
            except (TypeError, ValueError) as e:
                print(f'Ошибка в параметрах: "{e}". Воспользуйтесь справкой "help"')
                # raise e
        self.__ringerAwailable = False
        self.__ringer.join(timeout=2)


    def _todoHelp(self):
        '''Справка по командам'''
        actions = [(name, getattr(self, name).__doc__) for name in dir(self) if name.find('_todo', 0) == 0]
        for (name, text,) in actions:
            n = re.split(r'[A-Z]', name)
            i = 0
            for (k, nTail) in enumerate(n):
                if i > 0:
                    n[k] = name[i] + nTail
                    i += 1
                i += len(nTail)
            n.pop(0)
            name = '-'.join(n).lower()
            print('>', name, '-', '*' if text is None else text.strip())


    def _todoNewAlarm(self, time='', when=None, repeat=None):
        ''' установить новый будильник
            time - время чч:мм - обязательный
            when - дата (дд.мм.гггг) или дени недели через запятую (вт,чт,сб). Если не указано - звонит каждый день.
            repeat - повторы. формат: число:минуты, если пусто - звоним один раз'''
        # Если время не задали, надо спросить
        if not time:
            time = input('Введите время будильника "чч:мм": ')

        # не заданы дни звонка или точная дата
        if not when:
            when = input('В какие дни звонить (дни недели через запятую) или точная дата (дд.мм.гггг): ')

        # Запрос повторов ..
        if not repeat:
            repeat = input('Введите интервал число повторов в формате N:M - N раз чеез M минут (пустая строка - без повторов): ').strip()

        alaemMessage = input('Введите сообщение для будильника. Пустая строка - стандартное сообщение: ').strip()

        tbl = PrettyTable()
        tbl.title= 'Список мелодий'
        tbl.field_names = ['#', 'Наименование']
        for (i, sItem) in enumerate(soundFiles):
            tbl.add_row([i + 1, sItem.name])
        print(tbl)
        soundNum = input('Укажите мелодию звонка (пустая строка - звучит стандартная мелодия): ').strip()


        alarm = Alarm(time, when, repeat, alaemMessage, soundNum)
        if alarm.save():
            print(f'Будильник {alarm} успешно добавлен')


    def _todoList(self):
        ''' список будильников '''
        alarms = Alarm.getAll()
        tbl = PrettyTable()
        tbl.title= 'Список будильников'
        tbl.field_names = ['#', 'ID', 'Время', 'Условие', 'Повторы']
        for (i, alarm) in enumerate(alarms):
            tbl.add_row([i + 1, f"{'*' if alarm.isRing else ' '}{alarm.id}", alarm.time, alarm.when, alarm.repeats])
        print(tbl)


    def __getAlarmPerId(self, msg, aId=None):
        if aId is None:
            aId = input(msg).strip()
        return Alarm.getById(aId)


    def _todoStop(self, aId=None):
        ''' Остановка конкретного будильника '''
        alarm = self.__getAlarmPerId('Введите id будильника для остановки: ', aId)

        if alarm.stopDing():
            print(f'Будильник {alarm} остановлен')

    def _todoDelete(self, aId=None):
        ''' Удаление будильника по id'''
        alarm = self.__getAlarmPerId('Введите id будильника для удаления: ', aId)
        if alarm.delete():
            print(f'Будильник {alarm} удалён')
