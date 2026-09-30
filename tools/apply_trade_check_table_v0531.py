#!/usr/bin/env python3
from pathlib import Path

path = Path("mt5/Ramon.mq5")
if not path.exists():
    raise SystemExit("Run from ramon-ai-trader repo root")

s = path.read_text(encoding="utf-8")
if '#property version "1.530"' not in s:
    raise SystemExit("Expected Ramon v0.53.0 source")

s = s.replace('#property version "1.530"', '#property version "1.531"', 1)
s = s.replace('EA version: 0.53.0', 'EA version: 0.53.1')
s = s.replace('RAMON AI TRADER  v0.53.0 ', 'RAMON AI TRADER  v0.53.1 ')
s = s.replace(
    "UiRect(\"PANEL\",12,24,520,596,C'15,23,42',C'71,85,105');",
    "UiRect(\"PANEL\",12,24,1020,596,C'15,23,42',C'71,85,105');",
    1,
)

anchor = "   color sizing_color=DirectionColor(LastSizingSide);\n"
helpers = r'''
   // v0.53.1 display-only trade checklist helpers.
   bool checklist_spread_pass=(spread_points>0 && spread_points<=MaxSpreadPoints);
   bool checklist_volatility_ok=(LastAtr>0.0 && LastStopDistance>0.0 && LastTargetDistance>0.0);
   bool checklist_risk_exit_ok=(LastStopDistance>0.0 && LastTargetDistance>0.0);
   bool checklist_directional=(LastModelDecision=="BUY" || LastModelDecision=="SELL");
   bool checklist_entry_ready=checklist_directional && edge_pass && strength_pass
      && LastIntrabarConfirmed && LastAiTrendConfirmed
      && checklist_spread_pass && checklist_volatility_ok;
   string checklist_market_trend="UNCONFIRMED";
   color checklist_market_color=clrGold;
   if(LastAiTrendConfirmed && LastAiTrendDirection=="BUY")
   {
      checklist_market_trend="BULLISH";
      checklist_market_color=DirectionColor("BUY");
   }
   else if(LastAiTrendConfirmed && LastAiTrendDirection=="SELL")
   {
      checklist_market_trend="BEARISH";
      checklist_market_color=DirectionColor("SELL");
   }
   double checklist_rr=(LastStopDistance>0.0 ? LastTargetDistance/LastStopDistance : 0.0);
'''
if anchor not in s:
    raise SystemExit("Dashboard helper anchor not found")
s = s.replace(anchor, anchor + helpers, 1)

draw_start = s.index("void DrawDashboard()")
draw_end = s.index("bool CopyDiagnosticToClipboard()", draw_start)
draw = s[draw_start:draw_end]
chart_anchor = "   ChartRedraw();\n"

table = r'''
   // 10-point trade checklist table (DISPLAY ONLY).
   int tx=540, ty=108, tw=468, th=360, row_h=30;
   UiRect("CHECK_TABLE_BG",tx,ty,tw,th,C'17,27,46',C'71,85,105');
   UiRect("CHECK_TABLE_HEAD",tx+4,ty+4,tw-8,28,C'30,41,59',C'71,85,105');
   UiLabel("CHECK_TITLE","10-POINT TRADE CHECK",tx+12,ty+10,clrWhite,10);
   UiLabel("CHECK_SUMMARY",
      "MARKET: "+checklist_market_trend+"   MODEL: "+LastModelDecision
      +"   ENTRY: "+(checklist_entry_ready ? "READY" : "NOT READY"),
      tx+168,ty+10,(checklist_entry_ready ? state_color : clrGold),9);
   UiLabel("CHECK_H1","#",tx+12,ty+42,clrWhite,8);
   UiLabel("CHECK_H2","CHECK",tx+36,ty+42,clrWhite,8);
   UiLabel("CHECK_H3","LIVE VALUE",tx+174,ty+42,clrWhite,8);
   UiLabel("CHECK_H4","STATUS",tx+390,ty+42,clrWhite,8);

   int ry=ty+62;
   for(int r=0;r<10;r++)
      UiRect("CHECK_ROW_BG_"+IntegerToString(r+1),tx+4,ry+r*row_h,tw-8,row_h-2,
         (r%2==0 ? C'20,31,52' : C'15,24,42'),C'38,50,68');

   string c1=LastModelDecision+" | "+LastModelReason;
   string c2=checklist_market_trend+" | AI "+LastAiTrendDirection;
   string c3=DoubleToString(LastSignalStrength,3)+" / min "+DoubleToString(LastMinimumStrength,3);
   string c4=dominant+" "+DoubleToString(dominant_edge,2)+" / min "+DoubleToString(LastMinimumEdge,2);
   string c5=LastIntrabarDirection+" | move "+DoubleToString(LastIntrabarMoveAtr,3)
      +" | rb "+DoubleToString(LastIntrabarReboundAtr,3);
   string c6=LastAiTrendDirection+" | score "+DoubleToString(LastAiTrendScore,3)
      +" | path "+DoubleToString(LastAiTrendMoveAtr,3)
      +" | cons "+DoubleToString(LastAiTrendConsistency,2);
   string c7=(LastTargetStructureReady ? LastTargetMethod+" | "+LastTargetDirection : "target structure learning");
   string c8="ATR "+DoubleToString(LastAtr,2)+" | SL "+DoubleToString(LastStopDistance,2)
      +" | TP "+DoubleToString(LastTargetDistance,2);
   string c9="spread "+IntegerToString(spread_points)+" / max "+IntegerToString(MaxSpreadPoints);
   string c10="SL "+DoubleToString(LastStopDistance,2)+" | TP "+DoubleToString(LastTargetDistance,2)
      +" | RR "+DoubleToString(checklist_rr,2);

   string names[10]={"Direction","Market trend","Trend strength","Model edge","Intrabar timing",
      "AI trend","Market structure","Volatility","Entry cost","Risk / exit"};
   string vals[10]={c1,c2,c3,c4,c5,c6,c7,c8,c9,c10};
   string stats[10];
   color stat_colors[10];
   stats[0]=(checklist_directional ? "SIGNAL" : "WAIT");
   stats[1]=(LastAiTrendConfirmed ? "CONFIRMED" : "NOT READY");
   stats[2]=PassFail(strength_pass);
   stats[3]=PassFail(edge_pass);
   stats[4]=PassFail(LastIntrabarConfirmed);
   stats[5]=PassFail(LastAiTrendConfirmed);
   stats[6]=(LastTargetStructureReady ? "READY" : "LEARNING");
   stats[7]=(checklist_volatility_ok ? "DATA OK" : "INVALID");
   stats[8]=PassFail(checklist_spread_pass);
   stats[9]=(checklist_risk_exit_ok ? "DEFINED" : "INVALID");
   for(int i=0;i<10;i++)
      stat_colors[i]=clrWhite;
   stat_colors[0]=(checklist_directional ? state_color : clrWhite);
   stat_colors[1]=(LastAiTrendConfirmed ? checklist_market_color : clrGold);
   stat_colors[2]=(strength_pass ? clrWhite : clrGold);
   stat_colors[3]=(edge_pass ? DirectionColor(dominant) : clrGold);
   stat_colors[4]=(LastIntrabarConfirmed ? DirectionColor(LastIntrabarDirection) : clrGold);
   stat_colors[5]=(LastAiTrendConfirmed ? DirectionColor(LastAiTrendDirection) : clrGold);
   stat_colors[6]=(LastTargetStructureReady ? clrWhite : clrGold);
   stat_colors[7]=(checklist_volatility_ok ? clrWhite : clrTomato);
   stat_colors[8]=(checklist_spread_pass ? clrWhite : clrGold);
   stat_colors[9]=(checklist_risk_exit_ok ? clrWhite : clrTomato);

   for(int i=0;i<10;i++)
   {
      int cy=ry+7+i*row_h;
      UiLabel("CXN_"+IntegerToString(i),IntegerToString(i+1),tx+12,cy,clrWhite,8);
      UiLabel("CXK_"+IntegerToString(i),names[i],tx+36,cy,clrWhite,8);
      UiLabel("CXV_"+IntegerToString(i),vals[i],tx+174,cy,clrWhite,8);
      UiLabel("CXS_"+IntegerToString(i),stats[i],tx+390,cy,stat_colors[i],8);
   }

   UiLabel("CHECK_NOTE","DISPLAY ONLY - execution logic unchanged.",tx+12,ty+340,clrWhite,8);

'''
if chart_anchor not in draw:
    raise SystemExit("Chart redraw anchor not found")
draw = draw.replace(chart_anchor, table + chart_anchor, 1)
s = s[:draw_start] + draw + s[draw_end:]

path.write_text(s, encoding="utf-8")
print("Updated mt5/Ramon.mq5 to dashboard v0.53.1")
print("Display-only checklist added; execution logic untouched.")
