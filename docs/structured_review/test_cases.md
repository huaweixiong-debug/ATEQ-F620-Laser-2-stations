# F620 必须通过的单元测试矩阵 v1.1

## 测试环境

|字段|值|
|---|---|
|解释器|Y:\协众\101 ATEQ F620 双腔 + 激光打码\.venv\Scripts\python.exe / Python3.10.11|
|UI|QT_QPA_PLATFORM=offscreen|
|设备|Fake/Spy/Replay；不得连接串口、PLC、生产MySQL|
|文件|tmp_path内合成数据|
|计时|monotonic假时钟；避免长sleep|
|用例命名|新增每个test名称或parametrize ID必须含下列TC编号；全部编号至少映射一个可执行断言|
|保留|已有64用例不得删除、跳过或放宽|

## 决策函数

|ID|规则|Given|When|Then|负面副作用断言|
|---|---|---|---|---|---|
|TC01|BR08|first OK,single,production|P01|MARKING|无I/O|
|TC02|BR08|first OK,dual,production|P01|WAIT_2|无I/O|
|TC03|BR06|first NG; single/dual;sample开关全组合|P01|COMPLETE|无I/O|
|TC04|BR06|first UNKNOWN;全模式|P01|COMPLETE|无打码|
|TC05|BR07|first OK;single/dual;sample=True;mark_samples=False|P01|COMPLETE|无第二测|
|TC06|BR07/08|sample=True;mark_samples=True;single/dual|P01|MARKING/WAIT_2|无I/O|
|TC07|BR09|first OK;second OK;production|P02|MARKING|无I/O|
|TC08|BR09|任一NG或UNKNOWN；first None|P02|COMPLETE|无I/O|
|TC09|BR10|两OK;sample=True;mark_samples=False/True|P02|COMPLETE/MARKING|无I/O|
|TC10|BR08|mode非法或result非Result|P01/P02|ValueError/TypeError|无I/O|
|TC11|BR06..10|Result全枚举×mode×sample×mark_samples；second含None|P01/P02|与文档真值表一致|函数无外部依赖|

## 状态机与冻结

|ID|规则|Given|When|Then|负面副作用断言|
|---|---|---|---|---|---|
|TC12|BR01|station A;selection B|start_cycle|拒绝，IDLE|无ATEQ/DB/marker|
|TC13|BR02/05|完整选择，日期含空白|start_cycle|字段冻结；date_scheme strip；周期工位前缀|无实物IO|
|TC14|BR05|旧5位置参数|CycleSelection|date_scheme默认YYYYMMDD|兼容旧调用|
|TC15|BR05|date_scheme空/非str|CycleSelection|拒绝|无周期|
|TC16|BR05/18|非默认日期方案|journal恢复|date_scheme不丢失|不自动打码|
|TC17|BR06|首测NG，dual|test_first|COMPLETE，first存在，second=None|ATEQ run=1，marker=0|
|TC18|BR07|dual样件禁打|test_first|COMPLETE|ATEQ run=1，marker=0|
|TC19|BR08/09|生产dual两OK|两测+mark|WAIT_2→MARKING→COMPLETE|marker=1|
|TC20|BR09|首OK次NG|两测|COMPLETE|marker=0|
|TC21|BR03|external_start=True spy|test_first|只run|start_test调用0|
|TC22|BR03|模拟start_test callable|test_first|先start后run|与基线相同|
|TC23|BR03|response错station/cycle/program/sequence/timestamp/raw|test_first|FAULT+恢复要求|DB写0；marker0|
|TC24|BR22|ATEQ TimeoutError|test_first|FAULT+safe_stop，原异常传播|run1，不重试，不打码|
|TC25|BR21|DB insert/update抛异常|对应测量方法|FAULT+恢复要求|marker0，不重试|
|TC26|BR04|重复4边沿；4→非4→4|UI派发|去重；READY/WAIT_2准确派发|不重复运行同阶段|
|TC27|BR05|UI四个CycleSelection入口；自定义日期配置|构造/调用对应入口（聚焦UI或选择spy）|date_scheme进入selection和record|不改型号源文件|

## 权威回读门禁

|ID|规则|Given|When|Then|负面副作用断言|
|---|---|---|---|---|---|
|TC28|BR11|内存OK；get_committed=None|mark|False,FAULT,AMBIGUOUS|marker0；DB mark0|
|TC29|BR11|get_committed抛异常|mark|False,FAULT|marker0；缓存get调用0|
|TC30|BR11|内存内容X；提交内容Y且Y合格|mark|marker收到Y日期/型号/人员/数值/单位/时间|无内存回退|
|TC31|BR12|内存OK；DB first或second NG/UNKNOWN|mark|False,FAULT|marker0|
|TC32|BR12|回读非TraceRecord/错工位/错cycle|mark|False,FAULT|marker0|
|TC33|BR12|DB first缺失；dual second缺失|mark|False,FAULT|marker0|
|TC34|BR12|DB数值NaN/Inf/bool/非数字；first/second各字段|mark|False,FAULT|marker0|
|TC35|BR12/13|DB型号/人员空/非str；created_at非datetime；单位非str|mark|False,FAULT|marker0|
|TC36|BR12|single冻结；DB行反序列化默认dual，first OK，second None|mark|成功，8行中的P2/L2空|不要求schema存mode|
|TC37|BR12|single冻结；DB存在NG second|mark|False|marker0|
|TC38|BR11|缓存填假行；Mock连接返回另一行|MySQL get_committed|真实SELECT且内容来自DB|缓存不可短路|
|TC39|BR01/11|未知映射；只有info_B命中|MySQL get_committed|返回StationId.B|不静默只查A|
|TC40|BR01/11|A/B同时命中同cycle_id|MySQL get_committed|拒绝歧义|不随意选行|
|TC41|BR11|Fake插入；过程record后续变更|get_committed|保留插入快照|非共享对象|
|TC42|BR11|Fake update第二测/mark_marked|get_committed|显式提交快照更新；返回深拷贝|改返回值不污染仓储|
|TC43|BR17|accepted后DB marked抛错|mark|False,FAULT,AMBIGUOUS|marker1，不重打|
|TC44|BR17|marker rejected|mark|False,FAULT,AMBIGUOUS|DB marked0|

## 激光、权限与恢复

|ID|规则|Given|When|Then|负面副作用断言|
|---|---|---|---|---|---|
|TC45|BR14|文件发布/回读失败|LaserMarker.mark|拒绝回执|PLC任何读写0|
|TC46|BR15|文件OK；PLC正常；hold/settle0；关闭清空|mark|accepted；写序列True,False|True仅1次|
|TC47|BR16|True写/读高/hold/False写/读低/done超时任一点异常|mark|rejected；异常后尝试False|True不超过1次，无重发|
|TC48|BR16|主异常+清理False写失败|mark|receipt含断电未确认|不返回accepted|
|TC49|BR15/16/P09|done一直低；点位存在；无限单调假钟；hold/settle=0；清空关闭|mark到配置截止|拒绝；回执含完成位未置位原因；写序列True,False,False|True恰1次，False恰2次；不依赖固定时钟调用次数，不重发|
|TC50|BR19|operator；完成且marked|remark|PermissionError；原phase保留|marker0；DB0|
|TC51|BR19|admin；完成且marked|remark|新marker调用且正常完成|每次显式请求仅1次|
|TC52|BR18|未完成/INTENT/PULSED日志|重建控制器|FAULT；INTENT/PULSED→AMBIGUOUS|marker0|
|TC53|BR18|完整完成日志|重建控制器|COMPLETE，无恢复要求|marker0|
|TC54|BR20|required_samples2；NG,OK,NG,OK|calibration.sample|COMPLETE；ng_count=ok_count=2|顺序错不累计|
|TC55|BR20|period到期；管理员取消无理由/操作员取消|tick/cancel|到期锁定；非法取消拒绝|不启动实物测试|
|TC56|G05/06|全部旧测试+新测试；模拟smoke|统一执行|0失败；smoke COMPLETE marked|无真实设备连接|

## 执行结果格式

```json
{"source_commit":"c22accdf965bf25ea8b6bca14c10910a6f82d0c9","python":"3.10.11","baseline":{"passed":64},"focused":{"passed":null,"failed":null},"full":{"passed":null,"failed":null},"smoke":{"exit_code":null},"test_id_coverage":{"missing":[]},"physical_io_executed":false,"field_release":false}
```
