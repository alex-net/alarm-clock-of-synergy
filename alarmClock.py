import sqlite3, re, datetime
from prettytable import PrettyTable

from dotenv import dotenv_values

env = dotenv_values()




class AlarmClock:
    # дни недели
    DAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']

    def __init__(self):
        self.__con = sqlite3.connect(env.get('dbFile', 'ac.db') )
        cursor = self.__con.cursor()
        cursor.execute('''create table if not exists alarms (
            id integer,
            time integer  not null,
            cond text,
            depend integer,
            constraint pk primary key (id autoincrement),
            constraint idfk foreign key (depend) references alarms (id) on delete cascade
        );''')
        cursor.execute('create index if not exists depend_ind on alarms (depend)')
        cursor.close()
        self.__mainApp()
        self.__con.close()

    def __mainApp(self):
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
        time = re.match(r'^\d{2}:\d{2}$', time.strip())

        # Криво задан формат ..
        if time is None:
            raise TypeError('Не верный формат времени')
        time = list(map(lambda v: int(v), time.group(0).split(':')))
        # проверка предельных интервалов
        if time[0] > 23 or time[1] > 59:
            raise TypeError('Выход за пределы интервалов указания времени')
        time = time[1] + time[0] * 60


        # не заданы дни звонка или точная дата
        if not when:
            when = input('В какие дни звонить (дни недели через запятую) или точная дата (дд.мм.гггг): ')

        # думаем, что задана дата
        if when and when != '-':
            v = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', when.strip())
            if v is None:
                # с датой не получилось. пробуем разбить на части по запятой (вдруг это дни недели)
                v = [v for v in re.split(r'\s*,\s*', when) if self.DAYS.count(v) > 0]
            else:
                now = datetime.datetime.now()
                alarm = datetime.datetime(int(v.group(3)), int(v.group(2)), int(v.group(1)), time // 60, time % 60)
                sub = (alarm - now).total_seconds()
                if sub <= 0:
                    raise ValueError('Будильник не зазвонит: дата в прошлом')
            when = v.group(0) if isinstance(v, re.Match) else ','.join(v)
        else:
            when = None

        # Запрос повторов ..
        if not repeat:
            repeat = input('Введите интервал число повторов в формате N:M - N раз чеез M минут (пустая строка - без повторов): ').strip()

        if repeat and repeat != '-':
            r = re.match(r'^(\d+):(\d+)$', repeat)
            if r is None:
                raise TypeError('Неверно задан повтор будильника')
            repeat = {'count': int(r.group(1)), 'interval': int(r.group(2)) }
            if repeat['count'] * repeat['interval'] >= 24 * 60:
                raise ValueError('Слишком длинные повторы')
        else:
            repeat = None

        cursor = self.__con.cursor()
        # сохранение основного будильника
        cursor.execute('insert into alarms (time, cond) values(?, ?)', (time, when,))
        self.__con.commit()
        # Сохраняем повторы
        if repeat:
            time += repeat['interval']
            row = [time, when, cursor.lastrowid]
            for t in range(time, time + repeat['count'] * repeat['interval'], repeat['interval']):
                row[0] = t % (60 * 24)
                cursor.execute('insert into alarms (time, cond, depend) values(?, ?, ?)', row)
            self.__con.commit()
        cursor.close()
        print('Будильник успешно добавлен')


    def _todoList(self):
        ''' список будильников '''
        cursor = self.__con.cursor()
        cursor.execute('select * from alarms')
        rows = cursor.fetchall()
        cursor.close()

        alarms = [el for el in rows if el[3] is None]

        tbl = PrettyTable()
        tbl.title= 'Список будильников'
        tbl.field_names = ['#', 'ID', 'Время', 'Условие', 'Повторы']
        for (i, row) in enumerate(alarms):
            repeats = list(filter(lambda v: v[-1] == row[0], rows))
            tbl.add_row([i + 1, row[0], f'{row[1]//60}:{row[1]%60}', row[2], f'{len(repeats)} через {repeats[0][1]-row[1]} мин.' if repeats else '-'])
        # print(dir(tbl))
        print(tbl)


    def _todoDelete(self, aId=None):
        ''' Удаление будильника по id'''
        if aId is None:
            aId = input('Введите id будильника для удаления: ').strip()

        cursor = self.__con.cursor()
        cursor.execute(f'select id from alarms where id = ? ', (aId,))
        res = cursor.fetchone()
        if res:
            cursor.execute('delete from alarms where id = ?', res)
            cursor.execute('delete from alarms where depend = ?', res)
            self.__con.commit()
            print('Будильник удалён')
        else:
            raise ValueError('Будильник не найден')

        cursor.close()
