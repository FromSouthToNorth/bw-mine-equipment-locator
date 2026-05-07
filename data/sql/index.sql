-- 策略id: `8373`, 查询设备巷道工作面信息

declare @v_mineName nvarchar(32) = iif(left('@MineName', 1) = '@', N'', '@MineName'),
    @v_JSON nvarchar(max);

declare @MarkType TABLE
                  (
                      sys_id    nvarchar(32),
                      mark_type nvarchar(32)
                  )
insert into @MarkType
values ('120', 'B14'), -- 安全监测
       ('121', 'B16'), -- 工业视频
       ('140', 'B15');
-- 人员基站

-- =============================================
-- 1. 设备传感器
-- =============================================
declare @devices_json nvarchar(max);
select @devices_json = (select mfd.id,
                               pit.description as description,
                               mt.mark_type
                        from ms_form_device mfd
                                 join sys_pointinfo pit on mfd.id = pit.Name
                                 join @MarkType mt on mfd.sysid = mt.sys_id
                        where mfd.minename = @v_mineName
                          and mfd.sysid in ('120', '121', '140')
                        for json path);

-- =============================================
-- 2. 巷道
-- =============================================
declare @tunnels_json nvarchar(max);
select @tunnels_json = (select st.id,
                               replace(st.TunnelName, ' ', '')                                         as name,
                               dt.item_text                                                            as type,
                               st.Coalbed                                                              as coalbed,
                               -- 用户自定义的 line 已经是 JSON 数组字符串，用 JSON_QUERY 避免转义
                               JSON_QUERY(CONCAT('[', string_agg(CAST(concat(
                                       '{"x": ', cast(Pointgeometry.STX as decimal(18, 4)),
                                       ',"y":', cast(Pointgeometry.STY as decimal(18, 4)),
                                       ',"z":', cast(Pointgeometry.Z as decimal(10, 2)),
                                       '}') AS NVARCHAR(MAX)), ',') within group (order by Seq), ']')) as line
                        from sys_tunnel st
                                 join Sys_Traverse str on st.ID = str.TunnelID
                                 join dbo.MS_GetDictTextAndVal('ms_DM3DGIS_hdlx') dt
                                      on dt.item_value = st.TunnelType
                        where st.MineName = @v_mineName
                          and isnull(TunnelName, '') <> ''
                        group by st.id, st.TunnelName, st.Coalbed, dt.item_text
                        for json path);

-- =============================================
-- 3. 工作面
-- =============================================
declare @workfaces_json nvarchar(max);
select @workfaces_json = (select sw.WorkFaceName                                                         as workFaceName,
                                 dt.item_text                                                            as type,
                                 st.id                                                                   as tunnelId,
                                 JSON_QUERY(CONCAT('[', string_agg(CAST(concat(
                                         '{"x": ', cast(Pointgeometry.STX as decimal(18, 4)),
                                         ',"y":', cast(Pointgeometry.STY as decimal(18, 4)),
                                         ',"z":', cast(Pointgeometry.Z as decimal(10, 2)),
                                         '}') AS NVARCHAR(MAX)), ',') within group (order by Seq), ']')) as line
                          from sys_workface sw
                                   join sys_tunnel st on sw.id = st.WorkFaceID
                                   join Sys_Traverse str on st.ID = str.TunnelID
                                   join dbo.MS_GetDictTextAndVal('ms_DM3DGIS_hdlx') dt
                                        on st.TunnelType = dt.item_value
                          where sw.MineName = @v_mineName
                          group by sw.WorkFaceName, dt.item_text, st.id
                          order by sw.WorkFaceName
                          for json path);
select @devices_json, @tunnels_json, @workfaces_json
-- =============================================
-- 4. 合并为统一的 JSON 对象
-- -- =============================================
set @v_JSON = concat(
        '{"devices":', isnull(@devices_json, '[]'), ',',
        '"tunnels":', isnull(@tunnels_json, '[]'), ',',
        '"workfaces":', isnull(@workfaces_json, '[]'), '}'
              );

-- 输出验证
select @v_JSON as result_json;
