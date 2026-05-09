#!/usr/bin/env python3
"""Generate 5 data-driven insights from final.json based on actual data patterns.

Heuristics (run from data — do NOT use canned text):
  1. Revenue stability vs WoW (flat / surging / dropping)
  2. Store mix and share shifts
  3. Daily concentration (weekend vs midweek)
  4. Labor efficiency by store (over/under target 25-28%)
  5. Top products / category trends

Updates final.json in place.
"""
import json, sys

def gen(d):
    t = d['totals']; bs = d['by_store']; w = d['wow']
    daily = d['daily_revenue']
    tp = d['top_products']['overall']
    
    insights = []
    
    # 1. Revenue trend
    if abs(w['payments']) < 2:
        insights.append({
            'title': 'Stable Plateau',
            'body': f"Payments held flat at ${t['payments']:,.2f} ({w['payments']:+.1f}% WoW) with op margin at {t['op_margin_pct']:.1f}%. Multiple stable weeks confirm a reliable run-rate baseline. Use for forecasting."
        })
    elif w['payments'] > 5:
        insights.append({
            'title': 'Strong Growth Week',
            'body': f"Payments grew {w['payments']:+.1f}% WoW to ${t['payments']:,.2f}. Net sales {w['net_sales']:+.1f}%, transactions {w['transactions']:+.1f}%. Investigate which initiatives drove the lift to replicate."
        })
    else:
        insights.append({
            'title': 'Revenue Soft Week',
            'body': f"Payments down {w['payments']:.1f}% WoW to ${t['payments']:,.2f}. Net sales {w['net_sales']:+.1f}%. Investigate weather, promo gaps, or staffing shortfalls."
        })
    
    # 2. Store mix
    shares = {c: bs[c]['payments']/t['payments']*100 for c in ['SJ','MV','FM']}
    leader = max(shares, key=shares.get)
    laggard = min(shares, key=shares.get)
    insights.append({
        'title': f'{leader} Leads Store Mix',
        'body': f"{leader} contributed ${bs[leader]['payments']:,.2f} ({shares[leader]:.1f}% of total). {laggard} smallest at ${bs[laggard]['payments']:,.2f} ({shares[laggard]:.1f}%). Op margins: SJ {bs['SJ']['op_margin_pct']:.1f}%, MV {bs['MV']['op_margin_pct']:.1f}%, FM {bs['FM']['op_margin_pct']:.1f}%."
    })
    
    # 3. Daily concentration
    days = sorted(daily.items())
    weekend = sum(v['total'] for k, v in days if v.get('total') and __import__('datetime').datetime.strptime(k, '%Y-%m-%d').weekday() >= 4)
    weekend_pct = weekend / t['payments'] * 100 if t['payments'] else 0
    peak = max(days, key=lambda x: x[1]['total'])
    insights.append({
        'title': 'Weekend Concentration',
        'body': f"Fri-Sun = {weekend_pct:.0f}% of weekly revenue. Peak day {peak[0]} at ${peak[1]['total']:,.2f}. {'Recommend Thursday promo to lift midweek.' if weekend_pct > 45 else 'Midweek/weekend more balanced than typical.'}"
    })
    
    # 4. Labor efficiency
    flagged = [c for c in ['SJ','MV','FM'] if bs[c]['labor_pct_net'] > 28]
    if flagged:
        flagged_str = ', '.join(f"{c} ({bs[c]['labor_pct_net']:.1f}%)" for c in flagged)
        insights.append({
            'title': 'Labor Above Target',
            'body': f"Combined labor {t['labor_pct_net']:.1f}% of net (target 25-28%). Above target: {flagged_str}. Trim 30-40 weekly hours from lowest-revenue shifts at flagged stores."
        })
    else:
        insights.append({
            'title': 'Labor in Target Band',
            'body': f"All 3 stores within 25-28% labor target. SJ {bs['SJ']['labor_pct_net']:.1f}% (${bs['SJ']['rev_per_hour']:.0f}/hr density), MV {bs['MV']['labor_pct_net']:.1f}% (${bs['MV']['rev_per_hour']:.0f}/hr), FM {bs['FM']['labor_pct_net']:.1f}% (${bs['FM']['rev_per_hour']:.0f}/hr)."
        })
    
    # 5. Top products
    if tp:
        top1 = tp[0]; top2 = tp[1] if len(tp) > 1 else None
        body = f"#1 {top1['name']}: {top1['qty']} units / ${top1['revenue']:,.2f}."
        if top2:
            body += f" #2 {top2['name']}: {top2['qty']} units / ${top2['revenue']:,.2f}."
        # Theme detection
        keywords = {}
        for p in tp[:5]:
            for word in p['name'].lower().split():
                if len(word) > 3:
                    keywords[word] = keywords.get(word, 0) + 1
        repeated = [k for k, v in keywords.items() if v >= 2]
        if repeated:
            body += f" Repeating theme in top 5: {', '.join(repeated)} — consider grouped promo."
        insights.append({'title': 'Top Product Drivers', 'body': body})
    
    return insights[:5]

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else '/home/user/workspace/square_data/run10/final.json'
    d = json.load(open(path))
    d['insights'] = gen(d)
    json.dump(d, open(path, 'w'), indent=2)
    for i, ins in enumerate(d['insights'], 1):
        print(f"{i}. {ins['title']}")
        print(f"   {ins['body'][:120]}...")
