"""Shared public labels used by matching, specialist profiles and administration."""
TOPICS = {
    'anxiety':'Тревога и напряжение',
    'relationships':'Отношения с близкими',
    'self':'Самооценка и уверенность',
    'burnout':'Усталость и выгорание',
    'emotions':'Эмоции и раздражительность',
    'boundaries':'Личные границы',
    'family':'Семья и отношения с родителями',
    'loneliness':'Одиночество и поиск близости',
    'loss':'Утрата и расставание',
    'changes':'Перемены и адаптация',
    'work_study':'Работа и учёба',
    'sleep':'Сон и отдых',
}
BUDGETS = [['2500','До 2 500 ₽'],['5000','До 5 000 ₽'],['10000','До 10 000 ₽'],['30000','Любой бюджет']]
TIMES = [['any','Время не важно'],['morning','Утром · 06:00–12:00'],['day','Днём · 12:00–18:00'],['evening','Вечером · 18:00–00:00']]
TIME_RANGES = {'morning':[360,720],'day':[720,1080],'evening':[1080,1440]}

AGE_RANGES = {'under30':[18,29],'30_39':[30,39],'40_49':[40,49],'50plus':[50,100]}
AGES = [['any','Возраст не важен'],['under30','До 30 лет'],['30_39','30–39 лет'],['40_49','40–49 лет'],['50plus','50 лет и старше']]

def public():
    return {'topics':TOPICS,'budgets':BUDGETS,'times':TIMES,'timeRanges':TIME_RANGES,'ages':AGES,'ageRanges':AGE_RANGES}
