// Textos del módulo Contabilidad (español / inglés). El idioma se guarda en
// este navegador (aita.lang); por defecto, el del navegador.
const T = {
  es: {
    nav: 'Contabilidad', close: 'Cerrar', title: 'Contabilidad', subtitle: 'Lo que entra, lo que sale y lo que queda pendiente.',
    from: 'Desde', to: 'Hasta', thisMonth: 'Este mes', lastMonth: 'Mes pasado', thisYear: 'Este año',
    addIncome: 'Registrar ingreso', addExpense: 'Registrar gasto', export: 'Exportar reporte', categories: 'Categorías',
    income: 'Ingresos', expenses: 'Gastos', net: 'Ganancia neta', receivable: 'Por cobrar', payable: 'Por pagar',
    vsPrev: 'vs. período anterior', noPrev: 'sin datos del período anterior', overdueN: (n) => `${n} atrasada${n === 1 ? '' : 's'}`,
    openN: (n) => `${n} pendiente${n === 1 ? '' : 's'}`,
    chartMonthly: 'Ingresos y gastos por mes', chartCategories: 'Gastos por categoría', noChart: 'Todavía no hay datos para graficar',
    movements: 'Movimientos', date: 'Fecha', type: 'Tipo', category: 'Categoría', description: 'Descripción',
    method: 'Método de pago', status: 'Estado', amount: 'Cantidad', receipt: 'Recibo', actions: 'Acciones',
    t_income: 'Ingreso', t_expense: 'Gasto', all: 'Todos',
    s_paid: 'Pagado', s_pending: 'Pendiente', s_cancelled: 'Cancelado', s_overdue: 'Atrasado',
    m_cash: 'Efectivo', m_card: 'Tarjeta', m_bank: 'Banco / transferencia', m_check: 'Cheque', m_other: 'Otro',
    view: 'Ver', attach: 'Adjuntar', edit: 'Editar', cancel: 'Cancelar', fromPayments: 'De Pagos',
    noMovements: 'No hay movimientos en estas fechas', pendingTitle: 'Pendientes', addPending: '+ Agregar pendiente',
    toCollect: 'Por cobrar', toPay: 'Por pagar', due: 'Vence', who: 'Nombre', markPaid: 'Marcar como pagado',
    memberPayment: 'Pago de socio', noPending: 'Nada pendiente', late: 'Atrasado',
    newIncome: 'Nuevo ingreso', newExpense: 'Nuevo gasto', editMovement: 'Editar movimiento',
    notes: 'Notas', photo: 'Foto del recibo', photoHint: 'Foto o PDF, máximo 10 MB. Opcional.',
    noCategory: 'Sin categoría', save: 'Guardar', saved: 'Guardado', paidNow: 'Pagado', pendingOpt: 'Pendiente',
    descPh: 'Ej.: Mensualidad de Ana, alquiler de octubre…', required: 'Completa los campos marcados con *.',
    badAmount: 'Escribe una cantidad mayor que 0.', receiptFail: 'Se guardó el movimiento, pero no se pudo subir el recibo.',
    cancelTitle: 'Cancelar movimiento', cancelText: 'El movimiento queda en el historial como cancelado y deja de contar en los totales.',
    cancelBtn: 'Sí, cancelar', cancelled: 'Movimiento cancelado', newPending: 'Nueva cuenta pendiente',
    pendingKind: '¿Es un cobro o un pago?', payTitle: 'Marcar como pagado',
    payText: 'Se registrará automáticamente como ingreso o gasto con la fecha de hoy.', paid: 'Marcado como pagado',
    exportTitle: 'Exportar reporte', exportText: 'Resumen, movimientos, gastos por categoría y pendientes del período elegido. Es un reporte de gestión para tu contador; no es una declaración fiscal oficial.',
    csv: 'Excel (CSV)', pdf: 'PDF', catTitle: 'Categorías', addCat: 'Agregar', catName: 'Nombre',
    active: 'Activa', inactive: 'Inactiva', hide: 'Ocultar', show: 'Mostrar', staffNote: 'Puedes consultar y registrar movimientos. Para editar o cancelar, pide ayuda a un gerente.',
    err: { invalid_amount: 'La cantidad no es válida.', invalid_date: 'La fecha no es válida.', invalid_range: 'La fecha inicial es posterior a la final.',
      range_too_long: 'Elige un período de 3 años o menos.', unsupported_file: 'Solo fotos (JPG, PNG, WEBP, HEIC) o PDF.',
      file_too_large: 'El archivo supera 10 MB.', duplicate_category: 'Ya existe una categoría con ese nombre.',
      duplicate_source: 'Este pago ya está registrado.', forbidden: 'No tienes permiso para esta acción.',
      invalid_category: 'Elige una categoría válida.', invalid_description: 'Escribe una descripción.',
      invalid_counterparty: 'Escribe un nombre.', obligation_closed: 'Esta cuenta ya está cerrada.' },
  },
  en: {
    nav: 'Accounting', close: 'Close', title: 'Accounting', subtitle: 'What comes in, what goes out and what is still pending.',
    from: 'From', to: 'To', thisMonth: 'This month', lastMonth: 'Last month', thisYear: 'This year',
    addIncome: 'Record income', addExpense: 'Record expense', export: 'Export report', categories: 'Categories',
    income: 'Income', expenses: 'Expenses', net: 'Net profit', receivable: 'To collect', payable: 'To pay',
    vsPrev: 'vs. previous period', noPrev: 'no data for the previous period', overdueN: (n) => `${n} overdue`,
    openN: (n) => `${n} open`,
    chartMonthly: 'Income vs. expenses by month', chartCategories: 'Expenses by category', noChart: 'No data to chart yet',
    movements: 'Transactions', date: 'Date', type: 'Type', category: 'Category', description: 'Description',
    method: 'Payment method', status: 'Status', amount: 'Amount', receipt: 'Receipt', actions: 'Actions',
    t_income: 'Income', t_expense: 'Expense', all: 'All',
    s_paid: 'Paid', s_pending: 'Pending', s_cancelled: 'Cancelled', s_overdue: 'Overdue',
    m_cash: 'Cash', m_card: 'Card', m_bank: 'Bank / transfer', m_check: 'Check', m_other: 'Other',
    view: 'View', attach: 'Attach', edit: 'Edit', cancel: 'Cancel', fromPayments: 'From Payments',
    noMovements: 'No transactions in these dates', pendingTitle: 'Pending', addPending: '+ Add pending item',
    toCollect: 'To collect', toPay: 'To pay', due: 'Due', who: 'Name', markPaid: 'Mark as paid',
    memberPayment: 'Member payment', noPending: 'Nothing pending', late: 'Overdue',
    newIncome: 'New income', newExpense: 'New expense', editMovement: 'Edit transaction',
    notes: 'Notes', photo: 'Receipt photo', photoHint: 'Photo or PDF, up to 10 MB. Optional.',
    noCategory: 'No category', save: 'Save', saved: 'Saved', paidNow: 'Paid', pendingOpt: 'Pending',
    descPh: 'e.g. Ana’s monthly fee, October rent…', required: 'Fill in the fields marked with *.',
    badAmount: 'Enter an amount greater than 0.', receiptFail: 'The transaction was saved, but the receipt could not be uploaded.',
    cancelTitle: 'Cancel transaction', cancelText: 'It stays in the history as cancelled and no longer counts in the totals.',
    cancelBtn: 'Yes, cancel', cancelled: 'Transaction cancelled', newPending: 'New pending item',
    pendingKind: 'Money to collect or to pay?', payTitle: 'Mark as paid',
    payText: 'It will be recorded automatically as income or expense with today’s date.', paid: 'Marked as paid',
    exportTitle: 'Export report', exportText: 'Summary, transactions, expenses by category and pending items for the selected period. A management report for your accountant — not an official tax return.',
    csv: 'Excel (CSV)', pdf: 'PDF', catTitle: 'Categories', addCat: 'Add', catName: 'Name',
    active: 'Active', inactive: 'Hidden', hide: 'Hide', show: 'Show', staffNote: 'You can view and record transactions. Ask a manager to edit or cancel one.',
    err: { invalid_amount: 'The amount is not valid.', invalid_date: 'The date is not valid.', invalid_range: 'The start date is after the end date.',
      range_too_long: 'Choose a period of 3 years or less.', unsupported_file: 'Photos (JPG, PNG, WEBP, HEIC) or PDF only.',
      file_too_large: 'The file is larger than 10 MB.', duplicate_category: 'A category with that name already exists.',
      duplicate_source: 'This payment is already recorded.', forbidden: 'You do not have permission for this.',
      invalid_category: 'Choose a valid category.', invalid_description: 'Write a description.',
      invalid_counterparty: 'Write a name.', obligation_closed: 'This item is already closed.' },
  },
};

// El idioma es el del panel (../i18n.js); este archivo solo aporta los textos.
export { getLang, setLang } from '../i18n.js';
import { getLang } from '../i18n.js';

export function t(key, ...args) { const v = T[getLang()][key] ?? T.en[key] ?? key; return typeof v === 'function' ? v(...args) : v; }
export function errText(e) { return T[getLang()].err[e?.code] || e?.message || 'Error'; }

// Fechas "date" de Postgres (YYYY-MM-DD) en el idioma de esta pantalla.
export function fmtDay(d) {
  if (!d) return '—';
  const [y, m, day] = String(d).slice(0, 10).split('-').map(Number);
  return new Intl.DateTimeFormat(getLang() === 'es' ? 'es-US' : 'en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(y, m - 1, day)));
}
