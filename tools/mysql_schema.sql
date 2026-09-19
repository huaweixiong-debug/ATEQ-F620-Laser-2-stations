-- schema v2：ATEQ F620 双腔 + 激光打码（无扫码工序）
-- 两台电脑的 MySQL 结构完全相同（info_A + info_B 都建），
-- A 实例只写 info_A，B 实例只写 info_B。
-- 执行：mysql -u root -p < tools/mysql_schema.sql

CREATE DATABASE IF NOT EXISTS `test` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `test`;

CREATE TABLE IF NOT EXISTS `info_A` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `cycle_id` VARCHAR(80) NOT NULL,
  `Time` DATETIME(6) NOT NULL,
  `Part No.` VARCHAR(80) NOT NULL,
  `1 Pressure` DECIMAL(18,6) NULL,
  `1 Pressure Unit` VARCHAR(32) NOT NULL DEFAULT '',
  `1 Leakage` DECIMAL(18,6) NULL,
  `1 Leakage Unit` VARCHAR(32) NOT NULL DEFAULT '',
  `2 Pressure` DECIMAL(18,6) NULL,
  `2 Pressure Unit` VARCHAR(32) NOT NULL DEFAULT '',
  `2 Leakage` DECIMAL(18,6) NULL,
  `2 Leakage Unit` VARCHAR(32) NOT NULL DEFAULT '',
  `Result` VARCHAR(16) NOT NULL,
  `Person` VARCHAR(80) NOT NULL,
  `marked` TINYINT(1) NOT NULL DEFAULT 0,
  `Mark Time` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_info_A_cycle_id` (`cycle_id`),
  KEY `idx_info_A_time` (`Time`),
  KEY `idx_info_A_part` (`Part No.`),
  KEY `idx_info_A_marked` (`marked`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `info_B` LIKE `info_A`;
ALTER TABLE `info_B` RENAME INDEX `uq_info_A_cycle_id` TO `uq_info_B_cycle_id`;
ALTER TABLE `info_B` RENAME INDEX `idx_info_A_time` TO `idx_info_B_time`;
ALTER TABLE `info_B` RENAME INDEX `idx_info_A_part` TO `idx_info_B_part`;
ALTER TABLE `info_B` RENAME INDEX `idx_info_A_marked` TO `idx_info_B_marked`;
