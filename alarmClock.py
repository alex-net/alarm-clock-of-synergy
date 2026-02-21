import sqlite3, re, datetime, json
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
    constraint pk primary key (id autoincrement)
);''')
cursor.close()



class Alarm:
    ''' Класс одного будильника '''
    # дни недели
    DAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    # Наличие ошибок валидации
    __hasErrors = False


    @classmethod
    def getAll(cls):
        ''' достать все записи будильников '''
        cursor = dbCon.cursor()
        cursor.execute('select * from alarms')
        rows = cursor.fetchall()
        cursor.close()
        return [cls(dict(zip(('id', 'time', 'cond'), row))) for row in rows]


    @classmethod
    def getById(cls, aId):
        ''' загрузить будильник по id '''
        cursor = dbCon.cursor()
        cursor.execute('select * from alarms where id = ?', (aId,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            raise ValueError('Будильник не найден')
        return cls(dict(zip(('id', 'time', 'cond'), row)))


    def __init__(self, *args):
        self._id = None
        # проверка на наличие в первом аргументе словаря - словарь - данные из базы
        if len(args) == 1 and isinstance(args[0], dict):
            for k in args[0].keys():
                setattr(self, '_' + k, json.loads(args[0][k]) if k == 'cond' else args[0][k])
            return

        # создание нового объекта из исходных данных
        if len(args) == 3:
            try:
                self.__initFrom3Args(*args)
            except BaseException as e:
                self.__hasErrors = True
                raise e


    def __initFrom3Args(self, time='', when=None, repeat=None):
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
        if repeat and repeat != '-':
            r = re.match(r'^(\d+):(\d+)$', repeat)
            if r is None:
                raise TypeError('Неверно задан повтор будильника')
            repeat = {'count': int(r.group(1)), 'interval': int(r.group(2)) }
            if repeat['count'] * repeat['interval'] >= 24 * 60:
                raise ValueError('Слишком длинные повторы')
            self._cond.update(repeat)


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
        cursor = dbCon.cursor()
        cursor.execute('delete from alarms where id = ?', (self._id,))
        dbCon.commit()
        cursor.close()
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
            res.append(f'через {self._cond["count"]} мин')
        return ' '.join(res) + ']'


    @property
    def id(self):
        ''' возвращаем id будильника '''
        return self._id


    @property
    def time(self):
        ''' возвращаем время звонка'''
        return f'{self._time // 60}:{self._time % 60}'


    @property
    def when(self):
        ''' возвращаем ограничения на звонок '''
        return self._cond['date'] if 'date' in self._cond else  ', '.join(self._cond['days']) if 'days' in self._cond else 'каждый день'


    @property
    def repeats(self):
        ''' число повторов '''
        if 'count' in self._cond and 'interval' in self._cond:
            return f'{self._cond['count']} через {self._cond['interval']} мин.'
        return '-'


class AlarmClock:
    ''' Класс управления будильниками '''
    def __init__(self):
        ''' главный цикл приложения '''
        print('Для справки введите "help"\nвыход - пустая команда')
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
            when - дата (дд.мм.гггг) или дени недели через запятую (вт,чт,сб). Если не указано - звонит кажды йдень.
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

        alarm = Alarm(time, when, repeat)
        if alarm.save():
            print(f'Будильник {alarm} успешно добавлен')


    def _todoList(self):
        ''' список будильников '''
        alarms = Alarm.getAll()
        tbl = PrettyTable()
        tbl.title= 'Список будильников'
        tbl.field_names = ['#', 'ID', 'Время', 'Условие', 'Повторы']
        for (i, alarm) in enumerate(alarms):
            tbl.add_row([i + 1, alarm.id, alarm.time, alarm.when, alarm.repeats])
        print(tbl)


    def _todoDelete(self, aId=None):
        ''' Удаление будильника по id'''
        if aId is None:
            aId = input('Введите id будильника для удаления: ').strip()
        alarm = Alarm.getById(aId)
        if alarm.delete():
            print(f'Будильник {alarm} удалён')
