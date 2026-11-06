/* Localized minimal date and section labels for entity layouts. */
export const labels = {
  sr: { person:'Личност',event:'Догађај',place:'Место',sources:'Извори',evidence:'Докази',
    events:'Хронологија догађаја',relations:'Повезане личности',places:'Повезана места',
    people:'Личности',media:'Документи и медији',map:'Карта догађаја',unknown:'Датум није наведен',about:'око' },
  ru: { person:'Персона',event:'Событие',place:'Место',sources:'Источники',evidence:'Свидетельства',
    events:'Хронология событий',relations:'Связанные личности',places:'Связанные места',
    people:'Личности',media:'Документы и медиа',map:'Карта событий',unknown:'Дата не указана',about:'ок.' },
  en: { person:'Person',event:'Event',place:'Place',sources:'Sources',evidence:'Evidence',
    events:'Event chronology',relations:'Related people',places:'Related places',
    people:'People',media:'Documents and media',map:'Event map',unknown:'Date unknown',about:'c.' },
};
export function formatEntityDate(value, locale='sr') {
  if (value === undefined || value === null) return '';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  if (typeof value !== 'object') return '';
  if (value.text) return value.text;
  if (value.year === undefined) return '';
  const year = String(value.year);
  const two = (num) => String(num).padStart(2,'0');
  const core = value.month ? `${year}-${two(value.month)}${value.day ? '-'+two(value.day) : ''}` : year;
  const qualifier = value.before ? (locale === 'en' ? 'before ' : locale === 'ru' ? 'до ' : 'пре ')
    : value.after ? (locale === 'en' ? 'after ' : locale === 'ru' ? 'после ' : 'после ')
    : value.circa ? labels[locale].about+' ' : '';
  return qualifier + core;
}
export function sortEventRows(a,b) {
  const k = (value) => {
    if (typeof value === 'number') return value * 10000;
    if (typeof value === 'string') {
      const m = /^(-?\d{1,6})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(value);
      return m ? Number(m[1])*10000+Number(m[2]??0)*100+Number(m[3]??0) : Number.POSITIVE_INFINITY;
    }
    return typeof value?.year === 'number' ? value.year*10000+(value.month??0)*100+(value.day??0) : Number.POSITIVE_INFINITY;
  };
  return k(a.date) - k(b.date) || a.title.localeCompare(b.title);
}
