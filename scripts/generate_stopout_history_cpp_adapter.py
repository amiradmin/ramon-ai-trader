#!/usr/bin/env python3
"""Offline C++ execution adapter for the production Stop Out prefix only.

Not native MT5/Wine acceptance. Account/history/globals are in-memory mocks;
exposure and margin logic are deliberately excluded, never claimed as tested.
"""
from pathlib import Path
import hashlib
import re

from generate_stopout_history_harness import MOCKS, CASES
from generate_stopout_restart_probe import ROOT


def create_cpp_adapter(source: str) -> str:
    start = source.index('string V2StopoutKey()')
    end = source.index('   if(!EnableV2AccountRiskGuard) return true;', start)
    prefix = source[start:end]
    if prefix.count('uchar data[],key[],digest[];') != 1:
        raise ValueError('Production array declaration changed; review adapter')
    converted = prefix.replace('uchar data[],key[],digest[];',
                               'std::vector<uchar> data,key,digest;')
    converted += '   reason="adapter exposure path excluded"; return false;\n}\n'
    cases = CASES.replace('18446744073709551600,', '18446744073709551600ULL,')
    expected_scope = hashlib.sha256(b'123|FIXTURE-A').hexdigest()[:32]
    result = (ADAPTER + '\n// Production Stop Out prefix SHA256: '
              + hashlib.sha256(prefix.encode()).hexdigest() + '\n'
              + MOCKS + converted + cases
              + '\nint main(){ ResetFixture();\n'
              + f'if(V2StopoutScope()!="{expected_scope}") return 2;\n'
              + 'OnStart(); return Failures==0 && Cases==18 ? 0 : 1; }\n')
    if re.search(r'\b(?:OrderSend\w*|CTrade|WebRequest|FileOpen)\b|\bTrade\s*\.', result):
        raise ValueError('Execution/IO dependency found; refusing adapter')
    return result


ADAPTER = r'''
#include <openssl/sha.h>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>
using string=std::string;
using uchar=unsigned char;
using datetime=long;
using ENUM_ACCOUNT_INFO_INTEGER=int;
using ENUM_ACCOUNT_INFO_STRING=int;
using ENUM_TERMINAL_INFO_INTEGER=int;
using ENUM_DEAL_PROPERTY_INTEGER=int;
using ENUM_ORDER_TYPE=int;
static_assert(sizeof(long)==8 && sizeof(ulong)==8,"64-bit Linux adapter required");
const int ACCOUNT_LOGIN=1,ACCOUNT_SERVER=2,TERMINAL_CONNECTED=3;
const int DEAL_REASON=4,DEAL_TIME_MSC=5,DEAL_REASON_SO=6,DEAL_REASON_CLIENT=7;
const int ORDER_TYPE_SELL=8,CRYPT_HASH_SHA256=9,WHOLE_ARRAY=-1,CP_UTF8=65001;
const int DEAL_TIME=10,DEAL_SYMBOL=11,DEAL_POSITION_ID=12;
const int TIME_DATE=1,TIME_SECONDS=2;
const int ACCOUNT_EQUITY=13,ACCOUNT_MARGIN=14,ACCOUNT_MARGIN_FREE=15;
const double V2MinimumMarginLevelPercent=100.0;
string IntegerToString(long value){return std::to_string(value);}
int StringLen(const string &value){return value.size();}
int StringFind(const string &value,const string &pattern){
    auto offset=value.find(pattern);return offset==string::npos ? -1 : (int)offset;
}
bool MathIsValidNumber(double value){return std::isfinite(value);}
double MathFloor(double value){return std::floor(value);}
void ArrayResize(std::vector<uchar> &values,int size){values.resize(size);}
int StringToCharArray(const string &value,std::vector<uchar> &out,int,int,int){
    out.assign(value.begin(),value.end());out.push_back(0);return out.size();
}
int CryptEncode(int method,const std::vector<uchar> &data,
                const std::vector<uchar> &,std::vector<uchar> &digest){
    if(method!=CRYPT_HASH_SHA256)return -1;
    digest.resize(SHA256_DIGEST_LENGTH);
    return SHA256(data.data(),data.size(),digest.data()) ? SHA256_DIGEST_LENGTH : -1;
}
template<typename... Args> string StringFormat(string format,Args... args){
    auto pos=format.find("%I64u");if(pos!=string::npos)format.replace(pos,5,"%lu");
    char output[512];int size=std::snprintf(output,sizeof(output),format.c_str(),args...);
    if(size<0 || size>=(int)sizeof(output))throw std::runtime_error("format overflow");
    return string(output,size);
}
template<typename... Args> void PrintFormat(string format,Args... args){
    std::cout<<StringFormat(format,args...)<<'\n';
}
template<typename... Args> void Print(Args... args){(std::cout<<...<<args);std::cout<<'\n';}
void TerminalClose(int){} // Fixture completion only; never controls a terminal.

// Offline-only stand-ins for dependencies used by guarded release helpers.
// These never connect to or mutate a real MT5 terminal/account.
int PositionsTotal(){return 0;}
int OrdersTotal(){return 0;}
bool AccountLockHealthy(){return true;}
double AccountInfoDouble(int property){
    if(property==ACCOUNT_EQUITY) return 1000.0;
    if(property==ACCOUNT_MARGIN) return 100.0;
    if(property==ACCOUNT_MARGIN_FREE) return 900.0;
    return 0.0;
}
string TimeToString(datetime value,int){return std::to_string(value);}
string HistoryDealGetString(ulong,int){return "XAUUSD";}
'''


if __name__ == '__main__':
    source = (ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')
    output = ROOT/'build/mt5-history-harness/stopout_history_adapter.cpp'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(create_cpp_adapter(source))
    print(output)
