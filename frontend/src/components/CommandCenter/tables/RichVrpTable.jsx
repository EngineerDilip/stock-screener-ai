import ScannerTable from '../ScannerTable';
import { SymbolCell } from '../Cells';
import { formatIv, formatPct } from '../format';

const columns = [
  { key: 'symbol', label: 'Symbol', render: (r) => <SymbolCell symbol={r.symbol} /> },
  { key: 'iv', label: 'ATM IV', align: 'right', render: (r) => formatIv(r.iv) },
  { key: 'hv', label: '20D HV', align: 'right', render: (r) => formatIv(r.hv) },
  {
    key: 'vrpPct',
    label: 'VRP',
    align: 'right',
    render: (r) => <span className="text-amber-400">{formatPct(r.vrpPct, 0)}</span>,
  },
];

export default function RichVrpTable({ rows = [] }) {
  return (
    <ScannerTable
      title="Top Rich VRP"
      subtitle="IV running richest vs. 20D realized -- premium selling candidates"
      columns={columns}
      rows={rows}
    />
  );
}
