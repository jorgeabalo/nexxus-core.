// Catálogo de máquinas y ejercicios del plan de entrenamiento.
// Lo usan el portal del socio y el Manager Panel (lo importa desde /m/assets).
// `key` es lo que se guarda en training_plan_items.exercise_key; cada key tiene
// su ilustración en /m/assets/img/exercises/<key>.svg. `cardio`: se mide en
// minutos en lugar de series x repeticiones.
export const EXERCISES = [
  { key: 'leg_press', es: 'Prensa de piernas', en: 'Leg press' },
  { key: 'leg_extension', es: 'Extensión de piernas', en: 'Leg extension' },
  { key: 'leg_curl', es: 'Curl femoral', en: 'Leg curl' },
  { key: 'hip_abduction', es: 'Abductores', en: 'Hip abduction' },
  { key: 'chest_press', es: 'Press de pecho', en: 'Chest press' },
  { key: 'pec_fly', es: 'Aperturas (pec deck)', en: 'Pec fly' },
  { key: 'shoulder_press', es: 'Press de hombros', en: 'Shoulder press' },
  { key: 'lat_pulldown', es: 'Jalón al pecho', en: 'Lat pulldown' },
  { key: 'seated_row', es: 'Remo sentado', en: 'Seated row' },
  { key: 'triceps_pushdown', es: 'Tríceps en polea', en: 'Triceps pushdown' },
  { key: 'biceps_curl', es: 'Curl de bíceps', en: 'Biceps curl' },
  { key: 'ab_crunch', es: 'Abdominales', en: 'Ab crunch' },
  { key: 'squat', es: 'Sentadilla', en: 'Squat' },
  { key: 'treadmill', es: 'Cinta de correr', en: 'Treadmill', cardio: true },
  { key: 'bike', es: 'Bicicleta estática', en: 'Stationary bike', cardio: true },
  { key: 'elliptical', es: 'Elíptica', en: 'Elliptical', cardio: true },
  { key: 'other', es: 'Otro ejercicio', en: 'Other exercise' },
];
const BY_KEY = Object.fromEntries(EXERCISES.map(e => [e.key, e]));

export const exercise = (key) => BY_KEY[key] || BY_KEY.other;
export const exerciseImg = (key) => `/m/assets/img/exercises/${BY_KEY[key] ? key : 'other'}.svg`;
// Nombre visible: el que escribió el manager, o el del catálogo en el idioma pedido.
export const exerciseName = (item, lang = 'es') => (item.name && item.name.trim()) || exercise(item.exercise_key)[lang === 'en' ? 'en' : 'es'];
