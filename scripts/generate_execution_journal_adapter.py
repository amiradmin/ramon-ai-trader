#!/usr/bin/env python3
"""Run extracted MQL coordinator with Linux file locks and synthetic broker APIs.

Not native MQL5, Wine locking, broker lifecycle or power-loss acceptance.
"""
from pathlib import Path
import sys
import re
from generate_stopout_history_cpp_adapter import ADAPTER

ROOT = Path(__file__).resolve().parents[1]


def extract(source, start, end):
    return source.split(start, 1)[1].split(end, 1)[0]


def create_adapter(source):
    core = extract(source, '// BEGIN V2 EXECUTION JOURNAL CORE', '// END V2 EXECUTION JOURNAL CORE')
    broker = extract(source, '// BEGIN V2 EXECUTION BROKER RECONCILIATION', '// END V2 EXECUTION BROKER RECONCILIATION')
    core = core.replace('uchar data[],key[],digest[];', 'std::vector<uchar> data,key,digest;')
    core = core.replace('string fields[];', 'std::vector<string> fields;')
    # Remove generic Stop Out mocks that conflict with journal-specific,
    # stateful implementations below.
    shared = ADAPTER
    removals = (
        ("const int DEAL_TIME=10,DEAL_SYMBOL=11,DEAL_POSITION_ID=12;",
         "const int DEAL_TIME=10;"),
        ("int PositionsTotal(){return 0;}", ""),
        ("bool AccountLockHealthy(){return true;}", ""),
        ('string HistoryDealGetString(ulong,int){return "XAUUSD";}', ""),
    )
    for old, new in removals:
        count = shared.count(old)
        if count != 1:
            sys.exit(
                f"STOP: expected one shared declaration {old!r}, "
                f"found {count}; file not written"
            )
        shared = shared.replace(old, new, 1)

    for forbidden in (
        "int PositionsTotal(){return 0;}",
        "bool AccountLockHealthy(){return true;}",
        'string HistoryDealGetString(ulong,int){return "XAUUSD";}',
        "DEAL_SYMBOL=11",
        "DEAL_POSITION_ID=12",
    ):
        if forbidden in shared:
            sys.exit(f"STOP: duplicate mock remains: {forbidden}; no changes made")

    return shared + API + core + broker + MAIN


API = r'''
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <chrono>
#include <thread>
#include <sstream>
#include <iomanip>
using ushort=unsigned short; using uint=unsigned int;
const int INVALID_HANDLE=-1,FILE_COMMON=1,FILE_READ=2,FILE_WRITE=4,FILE_TXT=8,FILE_ANSI=16;
const int SEEK_SET_IGNORED=0; // Linux SEEK_SET and SEEK_END match MQL values.
int LastError=0,FlushCount=0; bool Connected=true,Healthy=true,StorageFault=false;
string Scope="fixture-a",Mode="full",_Symbol="XAUUSD_l";
const int TRADE_RETCODE_DONE=10009,TRADE_RETCODE_DONE_PARTIAL=10010,TRADE_RETCODE_PLACED=10008;
const int TRADE_RETCODE_REJECT=10006,TRADE_RETCODE_INVALID=10013,TRADE_RETCODE_INVALID_VOLUME=10014;
const int TRADE_RETCODE_INVALID_PRICE=10015,TRADE_RETCODE_INVALID_STOPS=10016;
const int TRADE_RETCODE_TRADE_DISABLED=10017,TRADE_RETCODE_MARKET_CLOSED=10018,TRADE_RETCODE_NO_MONEY=10019;
const int ORDER_TYPE_BUY=0,ORDER_TICKET=10,ORDER_STATE=11,ORDER_STATE_FILLED=12,ORDER_SYMBOL=13;
const int ORDER_VOLUME_CURRENT=14,ORDER_VOLUME_INITIAL=15;
const int DEAL_ORDER=16,DEAL_ENTRY=17,DEAL_ENTRY_IN=18,DEAL_ENTRY_OUT=31,DEAL_ENTRY_OUT_BY=32,DEAL_TYPE=19,DEAL_TYPE_BUY=20,DEAL_TYPE_SELL=21;
const int DEAL_SYMBOL=22,DEAL_POSITION_ID=23,DEAL_VOLUME=24;
const int POSITION_IDENTIFIER=25,POSITION_SL=26,POSITION_VOLUME=27,POSITION_TYPE=28;
const int POSITION_TYPE_BUY=29,POSITION_TYPE_SELL=30,POSITION_PRICE_OPEN=31,POSITION_SYMBOL=32;
void ResetLastError(){LastError=0;} int GetLastError(){return LastError;}
double MathAbs(double x){return std::abs(x);}
long StringToInteger(string s){try{return std::stol(s);}catch(...){return -1;}}
double StringToDouble(string s){try{return std::stod(s);}catch(...){return NAN;}}
string DoubleToString(double d,int n){std::ostringstream out;out<<std::fixed<<std::setprecision(n)<<d;return out.str();}
ushort StringGetCharacter(string s,int n){return s.at(n);}
string StringSubstr(string s,int start,int count=-1){return s.substr(start,count<0?string::npos:count);}
int StringSplit(string s,char delimiter,std::vector<string> &out){
 out.clear();std::istringstream in(s);string part;while(std::getline(in,part,delimiter))out.push_back(part);return out.size();}
bool AccountLockHealthy(){return Healthy;} long TerminalInfoInteger(int){return Connected;}
string V2StopoutScope(){return Scope;} long TimeCurrent(){return 1800000000;}
bool FileIsExist(string path,int){return access(path.c_str(),F_OK)==0;}
int FileOpen(string path,int,int,int){
 int fd=open(path.c_str(),O_RDWR);if(fd<0)return -1;
 if(flock(fd,LOCK_EX|LOCK_NB)!=0){close(fd);return -1;}return fd;}
void FileClose(int fd){close(fd);}
bool FileSeek(int fd,long offset,int mode){if(lseek(fd,offset,mode)<0){LastError=1;return false;}return true;}
ulong FileTell(int fd){return lseek(fd,0,SEEK_CUR);}
bool FileIsEnding(int fd){struct stat st;fstat(fd,&st);return lseek(fd,0,SEEK_CUR)>=st.st_size;}
uint FileWriteString(int fd,string text){if(Mode=="write_error"){LastError=1;return 0;}auto n=write(fd,text.data(),text.size());if(n<0){LastError=1;return 0;}return n;}
void FileFlush(int fd){if((Mode=="flush_error" && ++FlushCount>=2) || fsync(fd)!=0)LastError=1;}
string FileReadString(int fd){string s;char c;ssize_t n;
 while((n=read(fd,&c,1))==1){if(c=='\n')break;if(c!='\r')s+=c;}
 if(n<0)LastError=1;return s;}
// One synthetic order/deal/position, deliberately no broker or network operations.
bool HistorySelect(long,long){return Mode!="history_error";}
bool HistorySelectByPosition(ulong position){return position==44 && Mode!="history_error";}
bool HistoryOrderSelect(ulong order){return order==42;}
long HistoryOrderGetInteger(ulong,int p){return p==ORDER_TICKET?42:(Mode=="placed"?0:ORDER_STATE_FILLED);}
string HistoryOrderGetString(ulong,int){return _Symbol;}
double HistoryOrderGetDouble(ulong,int p){return p==ORDER_VOLUME_CURRENT?0.0:0.01;}
int HistoryDealsTotal(){return 1;} ulong HistoryDealGetTicket(int){return 43;}
long HistoryDealGetInteger(ulong,int p){
 if(p==DEAL_ORDER)return Mode=="wrong_order"?99:42;
 if(p==DEAL_ENTRY)return Mode=="netting"?99:DEAL_ENTRY_IN;
 if(p==DEAL_TYPE)return Mode=="sell"?DEAL_TYPE_SELL:DEAL_TYPE_BUY;
 if(p==DEAL_POSITION_ID)return 44;return 0;}
string HistoryDealGetString(ulong,int){return _Symbol;}
double HistoryDealGetDouble(ulong,int){return Mode=="partial"?0.005:0.01;}
int PositionsTotal(){return Mode=="missing"?0:1;}
ulong PositionGetTicket(int){return 44;} bool PositionSelectByTicket(ulong){return true;}
long PositionGetInteger(int p){return p==POSITION_IDENTIFIER?44:(Mode=="sell"?POSITION_TYPE_SELL:POSITION_TYPE_BUY);}
string PositionGetString(int){return _Symbol;}
double PositionGetDouble(int p){if(p==POSITION_SL)return Mode=="no_sl"?0:1990;
 if(p==POSITION_VOLUME)return 0.01;return 2000;}
bool OrderCalcProfit(int,string,double,double,double,double &pnl){pnl=-20;return true;}
'''

MAIN = r'''
int main(int argc,char **argv){
 if(argc<4)return 2;string op=argv[1],id=argv[2];Mode=argv[3];
 if(argc>4)Scope=argv[4];
 if(op=="provision"){
  int fd=open(V2ExecPath(Scope).c_str(),O_CREAT|O_EXCL|O_WRONLY,0600);if(fd<0)return 3;
  string payload="RAMON_EXEC_V1|"+Scope;
  string line=payload+"|"+V2ExecHash(payload)+"\r\n";
  write(fd,line.data(),line.size());fsync(fd);close(fd);return 0;
 }
 V2ExecutionLease lease;string reason;
 if(!V2ExecBegin(id,reason)){std::cout<<"BLOCKED "<<reason<<std::endl;return 10;}
 if(op=="check"){std::cout<<"ALLOWED"<<std::endl;return 0;}
 int side=Mode=="sell"?ORDER_TYPE_SELL:ORDER_TYPE_BUY;
 if(!V2ExecReserve(id,side,2000,1990,0.01,op=="guardian"?7:0)){
  std::cout<<"PREPARE_FAILED"<<std::endl;return 11;}
 std::cout<<"PREPARED"<<std::endl;
 if(op=="hold"){std::this_thread::sleep_for(std::chrono::milliseconds(1500));return 0;}
 if(op=="crash")_exit(0);
 if(op=="cancel"){return V2ExecFinish("CANCELLED",0,0)?0:12;}
 if(op=="guardian" && !V2ExecAmend(ORDER_TYPE_BUY,2000,1990,0.01))return 13;
 if(op=="prepare")return 0;
 if(op=="crash_receipt"){if(!V2ExecFinish("RECEIPT",42,43))return 14;_exit(0);}
 if(Mode=="disconnect")Connected=false;
 if(Mode=="changed")Scope="fixture-b";
 if(Mode=="reject")V2ExecRecordResult(TRADE_RETCODE_REJECT,0,0);
 else if(Mode=="unknown_ids")V2ExecRecordResult(10031,42,43);
 else if(Mode=="timeout")V2ExecRecordResult(10012,0,0);
 else if(Mode=="placed")V2ExecRecordResult(10008,42,0);
 else if(Mode=="partial")V2ExecRecordResult(10010,42,43);
 else V2ExecRecordResult(10009,42,43);
 std::cout<<"PENDING="<<V2ExecPending<<std::endl;return 0;
}
'''

if __name__ == '__main__':
    path = ROOT / 'build/v2-execution-journal/coordinator.cpp'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(create_adapter((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')))
    print(path)
